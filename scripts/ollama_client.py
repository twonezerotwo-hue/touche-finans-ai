# -*- coding: utf-8 -*-
"""Ollama API baglantisi (v2 - firma bazli)."""

import base64
import io
import json
import logging
from pathlib import Path

import requests
from PIL import Image

from config import OLLAMA_URL, MODEL, TEXT_MODEL, TIMEOUT, MAX_IMAGE_SIZE

log = logging.getLogger(__name__)


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


def metne_sor(prompt: str) -> dict:
    """Görselsiz finans metnini yerel metin modeliyle JSON'a dönüştürür."""
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
