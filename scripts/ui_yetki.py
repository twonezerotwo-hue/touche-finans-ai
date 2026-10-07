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
ROLLER_DOSYASI = Path(VERI_DIR) / "ui_roller.json"
ROL_LISTESI = ["OWNER", "CFO", "FINANCE_MANAGER", "ACCOUNTANT", "PROJECT_MANAGER", "VIEWER"]
_LOCK = threading.RLock()
_ESLESTIRME = {"hash": "", "son": None}


def _roller_yukle():
    try:
        veri = json.loads(ROLLER_DOSYASI.read_text(encoding="utf-8"))
        return {str(k).strip().casefold(): v for k, v in (veri.get("kullanicilar") or {}).items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _roller_kaydet(veri):
    ROLLER_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
    gecici = ROLLER_DOSYASI.with_suffix(".tmp")
    gecici.write_text(json.dumps({"surum": 1, "kullanicilar": veri}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    os.replace(gecici, ROLLER_DOSYASI)


def rol_ata(kimlik, rol, faaliyetler=None, projeler=None):
    """Kullaniciya rol ve faaliyet/proje kapsami atar (server-side yetkilendirme)."""
    kimlik = str(kimlik or "").strip().casefold()
    if not kimlik:
        raise ValueError("Kullanıcı kimliği gerekli.")
    rol = str(rol or "VIEWER").strip().upper()
    if rol not in ROL_LISTESI:
        raise ValueError(f"Geçersiz rol: {rol}")
    kapsam = {"rol": rol,
              "faaliyetler": [str(x) for x in (faaliyetler or ["*"])],
              "projeler": [str(x) for x in (projeler or ["*"])]}
    veri = _roller_yukle()
    veri[kimlik] = kapsam
    _roller_kaydet(veri)
    return {"kimlik": kimlik, **kapsam}


def roller_ozeti():
    return [{"kimlik": k, **v} for k, v in sorted(_roller_yukle().items())]


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
        return {"yetkili": True, "yerel": True, "kimlik": "local-admin", "ad": "Yerel yönetici",
                "eslestirme_gerekli": False, "rol": "OWNER", "faaliyetler": ["*"], "projeler": ["*"]}
    if ts_kullanici:
        yetkili = ts_kullanici in _yukle()
        kapsam = _roller_yukle().get(ts_kullanici) or {}
        rol = kapsam.get("rol", "FINANCE_MANAGER") if yetkili else "VIEWER"
        return {
            "yetkili": yetkili, "yerel": False, "kimlik": ts_kullanici, "ad": ts_ad,
            "eslestirme_gerekli": not yetkili, "rol": rol,
            "faaliyetler": kapsam.get("faaliyetler", ["*"]),
            "projeler": kapsam.get("projeler", ["*"]),
        }
    return {"yetkili": False, "yerel": False, "kimlik": "", "ad": "", "eslestirme_gerekli": True,
            "rol": "VIEWER", "faaliyetler": [], "projeler": []}


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
