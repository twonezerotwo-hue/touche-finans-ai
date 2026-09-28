# -*- coding: utf-8 -*-
"""Belge turunu otomatik algilar."""

import base64
import io
import json
import requests
from pathlib import Path
from PIL import Image

from config import OLLAMA_URL, MODEL, TIMEOUT, MAX_IMAGE_SIZE
from belge_turleri import tur_listesi

TANIMA_PROMPT = """Bu gorsel bir belge fotografi. Su turlerden HANGISI oldugunu belirle:

- irsaliye (klasik irsaliye)
- fatura
- banka_dekontu
- hakedis (hakedis + metraj)
- cek
- sozlesme
- makbuz
- tutanak
- puantaj
- police (sigorta policesi)
- izin_ruhsat
- teminat_mektubu
- teklif (fiyat teklifi / proforma)
- sevk_irsaliye (e-sevk irsaliyesi)

SADECE su formatta cevap ver:
{"tur": "<tur_anahtari>", "guven": <0-100 arasi sayi>}

Aciklama yazma, sadece JSON."""


def tanimla(foto_yolu: Path):
    """(tur, guven) dondurur. Taniyamazsa ('', 0)."""
    img = Image.open(foto_yolu)
    if img.mode != "RGB":
        img = img.convert("RGB")

    oran = MAX_IMAGE_SIZE / max(img.size)
    if oran < 1:
        img = img.resize((int(img.size[0] * oran), int(img.size[1] * oran)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    gorsel_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    payload = {
        "model": MODEL,
        "prompt": TANIMA_PROMPT,
        "images": [gorsel_b64],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 100},
    }

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        ham = r.json().get("response", "").strip()
        veri = json.loads(ham)
        tur = veri.get("tur", "").strip().lower()
        guven = int(veri.get("guven", 0))

        if tur not in tur_listesi():
            return "", 0
        return tur, guven
    except Exception:
        return "", 0
