# -*- coding: utf-8 -*-
"""Web UI belge yukleme, inceleme ve onay kuyrugu."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from belge_dosyasi import BELGE_UZANTILARI
from config import FIRMALAR, GELEN_EVRAK, LOGLAR, TELEGRAM_MAX_DOSYA_MB
from dogrulayici import FAIL, deterministik_dogrula
from finans_cekirdegi import onay_oyu_ekle
from guvenlik import audit_yaz, finansal_fingerprint
from main import belge_dosyasi_hazirla, belgeyi_reddet, hazirlanan_belgeyi_yaz
from ui_yetki import aktor_numarasi

STATE_PATH = Path(LOGLAR) / "ui_belge_kuyrugu.json"
MAX_BYTES = TELEGRAM_MAX_DOSYA_MB * 1024 * 1024
_LOCK = threading.RLock()
_BEKLEYEN = {}


def _json_uyumlu(deger):
    if isinstance(deger, Path):
        return str(deger)
    if isinstance(deger, (date, datetime)):
        return deger.isoformat()
    if isinstance(deger, Decimal):
        return float(deger)
    if isinstance(deger, dict):
        return {str(k): _json_uyumlu(v) for k, v in deger.items()}
    if isinstance(deger, (list, tuple)):
        return [_json_uyumlu(v) for v in deger]
    return deger


def _yukle():
    global _BEKLEYEN
    try:
        veri = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        _BEKLEYEN = veri if isinstance(veri, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        _BEKLEYEN = {}


def _kaydet():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    gecici = STATE_PATH.with_suffix(".tmp")
    gecici.write_text(json.dumps(_json_uyumlu(_BEKLEYEN), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(gecici, STATE_PATH)


def _guvenli_ad(dosya_adi):
    ad = Path(str(dosya_adi or "belge")).name
    govde = re.sub(r"[^A-Za-z0-9._-]+", "_", ad).strip("._") or "belge"
    return govde[:120]


def yuklenen_dosyayi_kaydet(dosya_adi, icerik):
    if not isinstance(icerik, (bytes, bytearray)) or not icerik:
        raise ValueError("Dosya boş.")
    if len(icerik) > MAX_BYTES:
        raise ValueError(f"Dosya en fazla {TELEGRAM_MAX_DOSYA_MB} MB olabilir.")
    ad = _guvenli_ad(dosya_adi)
    uzanti = Path(ad).suffix.casefold()
    if uzanti not in BELGE_UZANTILARI:
        raise ValueError("Desteklenen dosyalar: PDF, JPG, JPEG, PNG, WEBP ve BMP.")
    GELEN_EVRAK.mkdir(parents=True, exist_ok=True)
    hedef = GELEN_EVRAK / f"ui_{datetime.now():%Y%m%d_%H%M%S_%f}_{ad}"
    hedef.write_bytes(bytes(icerik))
    return hedef


def belgeyi_hazirla(dosya_yolu, firma_key=None, aktor="ui"):
    if firma_key and firma_key not in FIRMALAR:
        raise ValueError("Firma bulunamadı.")
    hazir = belge_dosyasi_hazirla(dosya_yolu, manuel_firma=firma_key or None)
    if not hazir.get("basari"):
        if hazir.get("foto_yolu"):
            belgeyi_reddet(hazir, "ui_dogrulama_hatasi")
        raise ValueError(hazir.get("mesaj") or "Belge işlenemedi.")
    token = uuid.uuid4().hex[:24]
    with _LOCK:
        _BEKLEYEN[token] = {
            "token": token, "durum": "ONAY_BEKLIYOR", "aktor": str(aktor),
            "olusturma": datetime.now().isoformat(timespec="seconds"), "hazir": _json_uyumlu(hazir),
        }
        _kaydet()
    audit_yaz("ui_belge_beklemede", belge_id=hazir["belge_id"], firma=hazir["firma_key"], aktor=str(aktor))
    return ozet(token)


def _kayit_ozeti(kayit):
    return {
        "tarih": str(kayit.get("Tarih") or kayit.get("Vade_Tarihi") or ""),
        "cari": str(kayit.get("Cari") or kayit.get("Alici") or kayit.get("Tedarikci") or ""),
        "islem": str(kayit.get("Islem_Turu") or kayit.get("Urun") or kayit.get("Is_Kalemi") or ""),
        "tutar": kayit.get("Tutar") if kayit.get("Tutar") not in (None, "") else kayit.get("Toplam"),
        "pb": str(kayit.get("PB") or "TL"),
        "proje": str(kayit.get("Proje") or ""),
    }


def ozet(token):
    veri = _BEKLEYEN.get(token)
    if not veri:
        raise ValueError("Belge kuyruğunda bulunamadı.")
    hazir = veri["hazir"]
    dogrulama = hazir.get("dogrulama") or {}
    return {
        "token": token, "durum": veri["durum"], "olusturma": veri["olusturma"],
        "belge_id": hazir.get("belge_id"), "dosya": Path(str(hazir.get("foto_yolu") or "Belge")).name,
        "firma_key": hazir.get("firma_key"), "firma": FIRMALAR.get(hazir.get("firma_key"), {}).get("ad", ""),
        "tur": hazir.get("tur"), "motor": hazir.get("cikarim_motoru", "qwen2.5vl"),
        "dogrulama": {
            "durum": dogrulama.get("durum"), "skor": dogrulama.get("skor", 0),
            "uyarilar": dogrulama.get("uyarilar", [])[:8], "hatalar": dogrulama.get("hatalar", [])[:8],
        },
        "kayitlar": [_kayit_ozeti(x) for x in hazir.get("veri", {}).get("kayitlar", [])[:20]],
    }


def listele():
    with _LOCK:
        sonuc = [ozet(t) for t in _BEKLEYEN]
    return sorted(sonuc, key=lambda x: x["olusturma"], reverse=True)


def duzelt(token, degisiklikler, aktor):
    if not isinstance(degisiklikler, dict) or not degisiklikler:
        raise ValueError("Düzeltilecek alan bulunamadı.")
    temiz = {}
    for alan, deger in list(degisiklikler.items())[:30]:
        alan = str(alan).strip()
        if not alan or alan.startswith("_") or not re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9_ ]{1,60}", alan):
            raise ValueError(f"Geçersiz alan: {alan}")
        temiz[alan] = deger
    with _LOCK:
        bekleyen = _BEKLEYEN.get(token)
        if not bekleyen or bekleyen["durum"] != "ONAY_BEKLIYOR":
            raise ValueError("Belge düzeltmeye açık değil.")
        hazir = bekleyen["hazir"]
        kayitlar = hazir.get("veri", {}).get("kayitlar", [])
        if not kayitlar:
            raise ValueError("Düzeltilecek kayıt yok.")
        kayitlar[0].update(temiz)
        dogrulama = deterministik_dogrula(hazir["veri"], hazir["firma_key"], hazir["tur"])
        hazir["dogrulama"] = dogrulama
        for kayit in kayitlar:
            kayit["_fingerprint"] = finansal_fingerprint(kayit, hazir["firma_key"], hazir["tur"])
            kayit["_dogrulama_durumu"] = dogrulama["durum"]
        _kaydet()
    audit_yaz("ui_belge_duzeltme", belge_id=hazir["belge_id"], aktor=str(aktor), alanlar=list(temiz), durum=dogrulama["durum"])
    return ozet(token)


def onayla(token, aktor):
    with _LOCK:
        bekleyen = _BEKLEYEN.get(token)
        if not bekleyen or bekleyen["durum"] != "ONAY_BEKLIYOR":
            raise ValueError("Belge onay beklemiyor.")
        hazir = bekleyen["hazir"]
        if hazir.get("dogrulama", {}).get("durum") == FAIL:
            raise ValueError("FAIL durumundaki belge onaylanamaz.")
        toplam = 0.0
        for kayit in hazir.get("veri", {}).get("kayitlar", []):
            try:
                toplam += float(kayit.get("Tutar") or kayit.get("Toplam") or 0)
            except (TypeError, ValueError):
                pass
        oy = onay_oyu_ekle(hazir["belge_id"], aktor_numarasi(aktor), toplam)
        if not oy["tamam"]:
            bekleyen["onay"] = oy
            _kaydet()
            return {"tamam": False, "onay": oy, "belge": ozet(token)}
        basarili, mesaj = hazirlanan_belgeyi_yaz(hazir, onay_kaynagi=f"ui:{aktor}")
        if not basarili:
            raise RuntimeError(mesaj or "Excel kaydı başarısız.")
        bekleyen["durum"] = "TAMAMLANDI"
        bekleyen["tamamlanma"] = datetime.now().isoformat(timespec="seconds")
        _kaydet()
    return {"tamam": True, "onay": oy, "belge": ozet(token)}


def reddet(token, aktor):
    with _LOCK:
        bekleyen = _BEKLEYEN.get(token)
        if not bekleyen or bekleyen["durum"] != "ONAY_BEKLIYOR":
            raise ValueError("Belge reddetmeye açık değil.")
        belgeyi_reddet(bekleyen["hazir"], "ui_kullanici_reddi")
        bekleyen["durum"] = "REDDEDILDI"
        bekleyen["tamamlanma"] = datetime.now().isoformat(timespec="seconds")
        _kaydet()
    audit_yaz("ui_belge_reddi", belge_id=bekleyen["hazir"].get("belge_id"), aktor=str(aktor))
    return ozet(token)


_yukle()
