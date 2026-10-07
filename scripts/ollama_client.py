# -*- coding: utf-8 -*-
"""Ollama API baglantisi (v2 - firma bazli)."""

import base64
import io
import json
import logging
import threading
from pathlib import Path

import requests
from PIL import Image

from config import (
    EVREN_API_BASE,
    EVREN_API_KEY,
    EVREN_MODEL,
    MAX_IMAGE_SIZE,
    MODEL,
    OLLAMA_URL,
    TEXT_LLM_PROVIDER,
    TEXT_MODEL,
    TIMEOUT,
)

log = logging.getLogger(__name__)
_METIN_DURUMU = threading.local()


def gorsel_hazirla(foto_yolu: Path) -> str:
    """Gorseli 1024px'e kucultur ve base64 dondurur."""
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


def modele_sor(foto_yolu: Path, prompt: str) -> dict:
    """
    Ollama'ya gorsel + prompt gonderir, JSON dondurur.
    Basarisiz olursa exception firlatir.
    """
    gorsel_b64 = gorsel_hazirla(foto_yolu)

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "images": [gorsel_b64],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
        },
    }

    log.info(f"Ollama'ya gonderiliyor: {foto_yolu.name}")
    resp = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()

    ham = resp.json().get("response", "").strip()
    log.debug(f"Ham cevap (ilk 200): {ham[:200]}")

    return _json_parse(ham)


def _ollamaya_metni_sor(prompt: str) -> dict:
    payload = {
        "model": TEXT_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0.0, "num_predict": 1200},
    }
    log.info("Ollama metin modeline gönderiliyor: %s", TEXT_MODEL)
    resp = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    ham = resp.json().get("response", "").strip()
    return _json_parse(ham)


def _evrene_metni_sor(prompt: str) -> dict:
    if not EVREN_API_KEY:
        raise RuntimeError("EVREN_API_KEY tanımlı değil.")
    payload = {
        "model": EVREN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 1200,
        "stream": False,
    }
    log.info("EVREN metin modeline gönderiliyor: %s", EVREN_MODEL)
    resp = requests.post(
        f"{EVREN_API_BASE}/chat/completions",
        headers={"X-API-Key": EVREN_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    veri = resp.json()
    ham = str((((veri.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
    return _json_parse(ham)


def son_metin_saglayici() -> str:
    return getattr(_METIN_DURUMU, "saglayici", "bilinmiyor")


def metin_saglayici_sirasi():
    """Yapilandirmaya gore kullanilabilir metin modellerini siralar."""
    evren_oncelikli = TEXT_LLM_PROVIDER == "evren" and bool(EVREN_API_KEY)
    siralama = ("evren", "ollama") if evren_oncelikli else ("ollama", "evren")
    return tuple(x for x in siralama if x != "evren" or EVREN_API_KEY)


def metne_sor_saglayici(prompt: str, saglayici: str) -> dict:
    """Belirli metin saglayicisini cagirir ve son kullanilan modeli kaydeder."""
    if saglayici not in {"evren", "ollama"}:
        raise ValueError(f"Bilinmeyen metin sağlayıcısı: {saglayici}")
    if saglayici == "evren" and not EVREN_API_KEY:
        raise RuntimeError("EVREN_API_KEY tanımlı değil.")
    sonuc = _evrene_metni_sor(prompt) if saglayici == "evren" else _ollamaya_metni_sor(prompt)
    _METIN_DURUMU.saglayici = saglayici
    return sonuc


def metne_sor_yedek(prompt: str, haric: str | None = None) -> dict:
    """Birincil cevap yapisal olarak yetersizse diger saglayiciyi dener."""
    son_hata = None
    for saglayici in metin_saglayici_sirasi():
        if saglayici == haric:
            continue
        try:
            return metne_sor_saglayici(prompt, saglayici)
        except Exception as exc:
            son_hata = exc
            log.warning("%s yedek metin sağlayıcısı kullanılamadı: %s", saglayici.upper(), exc)
    raise RuntimeError(f"Yedek metin modeli kullanılamadı: {son_hata}")


def metne_sor(prompt: str) -> dict:
    """Metni seçili sağlayıcıya gönderir; hata halinde diğer sağlayıcıya düşer."""
    _METIN_DURUMU.saglayici = "bilinmiyor"
    son_hata = None
    for saglayici in metin_saglayici_sirasi():
        try:
            return metne_sor_saglayici(prompt, saglayici)
        except Exception as exc:
            son_hata = exc
            log.warning("%s metin sağlayıcısı kullanılamadı: %s", saglayici.upper(), exc)
    raise RuntimeError(f"Metin modeli kullanılamadı: {son_hata}")


def _json_parse(ham: str) -> dict:
    """LLM ciktisindan JSON ayiklar. Bircok formati dener."""
    # 1. Direkt JSON
    try:
        return json.loads(ham)
    except json.JSONDecodeError:
        pass

    # 2. ```json ... ``` blogu
    if "```" in ham:
        parcalar = ham.split("```")
        for p in parcalar:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            try:
                return json.loads(p)
            except Exception:
                continue

    # 3. Ilk { ile son } arasi
    bas = ham.find("{")
    son = ham.rfind("}")
    if bas != -1 and son != -1 and son > bas:
        try:
            return json.loads(ham[bas:son + 1])
        except Exception:
            pass

    raise ValueError(f"JSON parse edilemedi. Ham cikti: {ham[:500]}")
