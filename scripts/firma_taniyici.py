# -*- coding: utf-8 -*-
"""
Belgenin hangi firmaya ait oldugunu tespit eder.
Iki katmanli:
1. Anahtar kelime arama (hizli)
2. LLM sorma (akilli)
"""

import base64
import io
import json
import logging
import re
from pathlib import Path

import requests
from PIL import Image

from config import (
    FIRMA_ANAHTARLARI,
    FIRMALAR,
    MAX_IMAGE_SIZE,
    MODEL,
    OLLAMA_URL,
    TIMEOUT,
    VARSAYILAN_FIRMA,
)

log = logging.getLogger(__name__)


# ============================================================
# KATMAN 1: ANAHTAR KELIME ARAMA
# ============================================================

def _metin_temizle(metin):
    """Turkce karakterleri sadelestirip kucuk harfe cevirir."""
    if not metin:
        return ""
    metin = str(metin).lower()
    # Turkce karakterler
    ceviriler = {
        "ı": "i", "İ": "i", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ş": "s", "Ş": "s",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
        "â": "a", "Â": "a", "î": "i", "Î": "i",
        "û": "u", "Û": "u",
    }
    for tr, en in ceviriler.items():
        metin = metin.replace(tr.lower(), en)
    return metin


def _anahtar_kelime_ara(metin):
    """
    Metinde firma anahtar kelimelerini arar.
    Donus: (firma_key, eslesen_kelime) veya (None, None)
    """
    if not metin:
        return None, None

    metin_sade = _metin_temizle(metin)

    for firma_key, anahtarlar in FIRMA_ANAHTARLARI.items():
        for anahtar in anahtarlar:
            anahtar_sade = _metin_temizle(anahtar)
            if anahtar_sade and anahtar_sade in metin_sade:
                return firma_key, anahtar
    return None, None


def _dosya_adi_ara(foto_yolu):
    """Dosya adindan firma tahmini."""
    return _anahtar_kelime_ara(foto_yolu.name)


# ============================================================
# KATMAN 2: LLM SORMA
# ============================================================

FIRMA_SORMA_PROMPT = """Bu gorsel bir belge fotografi.
Belgenin hangi sirkete ait oldugunu tespit et.

Mevcut sirketler:
{firmalar}

SADECE su formatta cevap ver:
{{"firma": "<firma_key>", "guven": <0-100>, "sebep": "<kisa aciklama>"}}

Eger belgede sirket adi goremiyorsan firma olarak "belirsiz" yaz.
Aciklama yazma, sadece JSON."""


def _gorsel_b64(foto_yolu):
    """Gorseli base64'e cevirir."""
    img = Image.open(foto_yolu)
    if img.mode != "RGB":
        img = img.convert("RGB")
    oran = MAX_IMAGE_SIZE / max(img.size)
    if oran < 1:
        img = img.resize(
            (int(img.size[0] * oran), int(img.size[1] * oran)),
            Image.LANCZOS,
        )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _llm_firma_sor(foto_yolu):
    """LLM'e belgenin hangi firmaya ait oldugunu sorar."""
    firma_listesi = "\n".join(
        [f"- {k}: {v['ad']}" for k, v in FIRMALAR.items()]
    )

    prompt = FIRMA_SORMA_PROMPT.format(firmalar=firma_listesi)

    try:
        payload = {
            "model": MODEL,
            "prompt": prompt,
            "images": [_gorsel_b64(foto_yolu)],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 100},
        }

        r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        ham = r.json().get("response", "").strip()
        veri = json.loads(ham)

        firma = veri.get("firma", "").strip().lower()
        guven = int(veri.get("guven", 0))
        sebep = veri.get("sebep", "")

        if firma == "belirsiz" or firma not in FIRMALAR:
            return None, 0, sebep

        return firma, guven, sebep
    except Exception as e:
        log.warning(f"LLM firma sorma hatasi: {e}")
        return None, 0, str(e)


# ============================================================
# ANA FONKSIYON
# ============================================================

def tani(foto_yolu, metin=None):
    """
    Belgenin hangi firmaya ait oldugunu tespit eder.
    Donus: (firma_key, guven, kaynak)
        firma_key: tespit edilen firma anahtari
        guven:    0-100 arasi guven skoru
        kaynak:   'anahtar_kelime', 'llm', 'varsayilan', 'dosya_adi'
    """
    foto_yolu = Path(foto_yolu)

    if not foto_yolu.exists():
        log.error(f"Dosya yok: {foto_yolu}")
        return VARSAYILAN_FIRMA, 0, "hata"

    # 1. Dosya adindan ara
    firma, anahtar = _dosya_adi_ara(foto_yolu)
    if firma:
        log.info(f"Firma tespit: {firma} (dosya adi: '{anahtar}')")
        return firma, 90, "dosya_adi"

    # 2. Metinden ara (OCR sonucu varsa)
    if metin:
        firma, anahtar = _anahtar_kelime_ara(metin)
        if firma:
            log.info(f"Firma tespit: {firma} (metin: '{anahtar}')")
            return firma, 85, "anahtar_kelime"

    # 3. LLM'e sor
    log.info(f"LLM'e soruluyor: {foto_yolu.name}")
    firma, guven, sebep = _llm_firma_sor(foto_yolu)

    if firma and guven >= 60:
        log.info(f"Firma tespit (LLM): {firma} (%{guven}) - {sebep}")
        return firma, guven, "llm"

    # 4. Varsayilan
    log.warning(f"Firma tespit edilemedi, varsayilan: {VARSAYILAN_FIRMA}")
    return VARSAYILAN_FIRMA, 0, "varsayilan"


def firma_ozet():
    """Hata ayiklama icin tum firmalari listeler."""
    return [
        {
            "key": k,
            "ad": v["ad"],
            "anahtar_kelimeler": FIRMA_ANAHTARLARI.get(k, []),
        }
        for k, v in FIRMALAR.items()
    ]
