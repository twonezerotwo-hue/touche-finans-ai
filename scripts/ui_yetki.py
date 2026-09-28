# -*- coding: utf-8 -*-
"""Yerel UI ve Tailscale Serve icin kimlik/yetki katmani."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path

from config import VERI_DIR

YETKI_DOSYASI = Path(VERI_DIR) / "ui_yetkililer.json"
_LOCK = threading.RLock()
_ESLESTIRME = {"hash": "", "son": None}


def _yukle():
    try:
        veri = json.loads(YETKI_DOSYASI.read_text(encoding="utf-8"))
        return {str(x).strip().casefold() for x in veri.get("kullanicilar", []) if str(x).strip()}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _kaydet(kullanicilar):
    YETKI_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
    gecici = YETKI_DOSYASI.with_suffix(".tmp")
    gecici.write_text(
        json.dumps({"surum": 1, "kullanicilar": sorted(kullanicilar)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(gecici, YETKI_DOSYASI)


def oturum(headers, host, client_ip):
    """Yerel yonetici veya Tailscale kimligi dondurur."""
    host_adi = str(host or "").split(":", 1)[0].strip("[]").casefold()
    ts_kullanici = str(headers.get("Tailscale-User-Login") or "").strip().casefold()
    ts_ad = str(headers.get("Tailscale-User-Name") or ts_kullanici).strip()
    yerel = client_ip in {"127.0.0.1", "::1"} and host_adi in {"127.0.0.1", "localhost", "::1"}
    if yerel:
        return {"yetkili": True, "yerel": True, "kimlik": "local-admin", "ad": "Yerel yönetici", "eslestirme_gerekli": False}
    if ts_kullanici:
        yetkili = ts_kullanici in _yukle()
        return {
            "yetkili": yetkili, "yerel": False, "kimlik": ts_kullanici, "ad": ts_ad,
            "eslestirme_gerekli": not yetkili,
        }
    return {"yetkili": False, "yerel": False, "kimlik": "", "ad": "", "eslestirme_gerekli": True}


def kod_uret():
    kod = f"{secrets.randbelow(100_000_000):08d}"
    with _LOCK:
        _ESLESTIRME["hash"] = hashlib.sha256(kod.encode("ascii")).hexdigest()
        _ESLESTIRME["son"] = datetime.now() + timedelta(minutes=10)
    return {"kod": kod, "gecerlilik_dakika": 10}


def eslestir(kod, tailscale_kimligi):
    kimlik = str(tailscale_kimligi or "").strip().casefold()
    if not kimlik:
        raise ValueError("Tailscale kullanıcı kimliği alınamadı.")
    aday = hashlib.sha256(str(kod or "").strip().encode("ascii", errors="ignore")).hexdigest()
    with _LOCK:
        son = _ESLESTIRME.get("son")
        if not son or datetime.now() > son or not secrets.compare_digest(aday, _ESLESTIRME.get("hash", "")):
            raise ValueError("Eşleştirme kodu geçersiz veya süresi dolmuş.")
        kullanicilar = _yukle()
        kullanicilar.add(kimlik)
        _kaydet(kullanicilar)
        _ESLESTIRME.update({"hash": "", "son": None})
    return kimlik


def yetkililer():
    return sorted(_yukle())


def yetki_kaldir(kimlik):
    kullanicilar = _yukle()
    kullanicilar.discard(str(kimlik or "").strip().casefold())
    _kaydet(kullanicilar)


def aktor_numarasi(kimlik):
    """Mevcut coklu onay tablosu icin kararli, negatif 63-bit kimlik."""
    ozet = hashlib.sha256(str(kimlik).encode("utf-8")).digest()
    return -max(1, int.from_bytes(ozet[:8], "big") & ((1 << 63) - 1))
