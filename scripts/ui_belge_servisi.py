# -*- coding: utf-8 -*-
"""Web UI belge yukleme, inceleme ve onay kuyrugu."""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from belge_dosyasi import BELGE_UZANTILARI
from config import FIRMALAR, GELEN_EVRAK, LOGLAR, ONAY_PAROLASI_DOSYASI, TELEGRAM_MAX_DOSYA_MB
from dogrulayici import FAIL, deterministik_dogrula
from kurumsal_finans import onay_kararlari_temizle
from guvenlik import audit_yaz, finansal_fingerprint
from main import belge_dosyasi_hazirla, belgeyi_reddet, hazirlanan_belgeyi_yaz
from master_data import master_data_oku
from sohbet_kaydi import _adayi_dogrula, _norm as sohbet_norm, taslak_hazirla

STATE_PATH = Path(LOGLAR) / "ui_belge_kuyrugu.json"
MAX_BYTES = TELEGRAM_MAX_DOSYA_MB * 1024 * 1024
_LOCK = threading.RLock()
_BEKLEYEN = {}
_PAROLA_HATALARI = {}


def _b64_coz(value):
    metin = str(value or "")
    return base64.urlsafe_b64decode(metin + "=" * (-len(metin) % 4))


def _onay_parolasi_dogru_mu(parola):
    """Yerel dosyadaki PBKDF2 özetine karşı sabit süreli parola kontrolü."""
    try:
        ayar = json.loads(Path(ONAY_PAROLASI_DOSYASI).read_text(encoding="utf-8"))
        if ayar.get("algoritma") != "pbkdf2_sha256":
            return False
        hesaplanan = hashlib.pbkdf2_hmac(
            "sha256", str(parola or "").encode("utf-8"), _b64_coz(ayar.get("tuz")),
            int(ayar.get("iterasyon") or 260000),
        )
        return hmac.compare_digest(hesaplanan, _b64_coz(ayar.get("ozet")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _onay_parolasini_dogrula(parola, aktor):
    kimlik = str(aktor or "bilinmeyen")
    simdi = time.monotonic()
    hata = _PAROLA_HATALARI.get(kimlik, {"adet": 0, "kilit": 0.0})
    if hata["kilit"] > simdi:
        raise PermissionError("Çok fazla hatalı deneme. Beş dakika sonra tekrar deneyin.")
    if _onay_parolasi_dogru_mu(parola):
        _PAROLA_HATALARI.pop(kimlik, None)
        return
    adet = int(hata["adet"]) + 1
    _PAROLA_HATALARI[kimlik] = {"adet": adet, "kilit": simdi + 300 if adet >= 5 else 0.0}
    audit_yaz("ui_onay_parolasi_hatasi", aktor=kimlik, deneme=min(adet, 5))
    if adet >= 5:
        raise PermissionError("Onay şifresi hatalı. Beş dakika süreyle kilitlendi.")
    raise PermissionError("Onay şifresi hatalı.")


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


def _uyari_ozeti(deger):
    """Eski kuyruk kayıtlarındaki ham model sözlüklerini okunur metne çevirir."""
    if isinstance(deger, dict):
        return str(deger.get("mesaj") or deger.get("aciklama") or deger.get("uyari") or "").strip()
    metin = str(deger or "").strip()
    onek = "AI kayıt notu:"
    if metin.startswith(onek):
        ham = metin[len(onek):].strip()
        if ham.startswith("{") and ham.endswith("}"):
            try:
                nesne = ast.literal_eval(ham)
            except (ValueError, SyntaxError):
                nesne = None
            if isinstance(nesne, dict):
                temiz = str(nesne.get("mesaj") or nesne.get("aciklama") or nesne.get("uyari") or "").strip()
                if temiz:
                    return f"{onek} {temiz}"
    return metin


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


# Uzanti -> beklenen sihirli bayt imzasi (MIME dogrulamasi)
_IMZALAR = {
    ".pdf": b"%PDF",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".bmp": b"BM",
}


def _icerik_uzantiya_uygun(icerik, uzanti) -> bool:
    bas = bytes(icerik[:16])
    if uzanti == ".webp":
        return bas.startswith(b"RIFF") and bas[8:12] == b"WEBP"
    imza = _IMZALAR.get(uzanti)
    return bool(imza) and bas.startswith(imza)


def yuklenen_dosyayi_kaydet(dosya_adi, icerik):
    if not isinstance(icerik, (bytes, bytearray)) or not icerik:
        raise ValueError("Dosya boş.")
    if len(icerik) > MAX_BYTES:
        raise ValueError(f"Dosya en fazla {TELEGRAM_MAX_DOSYA_MB} MB olabilir.")
    ad = _guvenli_ad(dosya_adi)
    uzanti = Path(ad).suffix.casefold()
    if uzanti not in BELGE_UZANTILARI:
        raise ValueError("Desteklenen dosyalar: PDF, JPG, JPEG, PNG, WEBP ve BMP.")
    if not _icerik_uzantiya_uygun(icerik, uzanti):
        raise ValueError("Dosya içeriği uzantısıyla uyuşmuyor (MIME doğrulaması başarısız).")
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
    return _kuyruga_al(hazir, aktor, "belge")


def _kuyruga_al(hazir, aktor, kaynak_turu):
    token = uuid.uuid4().hex[:24]
    with _LOCK:
        _BEKLEYEN[token] = {
            "token": token, "durum": "ONAY_BEKLIYOR", "aktor": str(aktor),
            "olusturma": datetime.now().isoformat(timespec="seconds"),
            "kaynak_turu": kaynak_turu, "hazir": _json_uyumlu(hazir),
        }
        _kaydet()
    audit_yaz(f"ui_{kaynak_turu}_beklemede", belge_id=hazir["belge_id"], firma=hazir["firma_key"], aktor=str(aktor))
    return ozet(token)


def sohbet_taslagi_hazirla(metin, firma_key, aktor="ui"):
    hazir = taslak_hazirla(metin, firma_key)
    return _kuyruga_al(hazir, aktor, "sohbet")


def _kayit_ozeti(kayit):
    return {
        "faaliyet": str(kayit.get("Faaliyet") or ""),
        "tarih": str(kayit.get("Tarih") or kayit.get("Vade_Tarihi") or ""),
        "cari": str(kayit.get("Cari") or kayit.get("Alici") or kayit.get("Tedarikci") or ""),
        "islem": str(kayit.get("Islem_Turu") or kayit.get("Urun") or kayit.get("Is_Kalemi") or ""),
        "tutar": kayit.get("Tutar") if kayit.get("Tutar") not in (None, "") else kayit.get("Toplam"),
        "pb": str(kayit.get("PB") or "TL"),
        "kur": kayit.get("Kur"),
        "proje": str(kayit.get("Proje") or ""),
        "cari_tipi": str(kayit.get("Cari_Tipi") or ""),
        "hesap_kasa": str(kayit.get("Hesap_Kasa") or ""),
        "yeni_cari": bool(kayit.get("_yeni_cari")),
        "vade": str(kayit.get("Vade") or kayit.get("Vade_Tarihi") or ""),
        "aciklama": str(kayit.get("Aciklama") or "")[:240],
        "otomatik_eslesmeler": [str(x) for x in kayit.get("_otomatik_eslesmeler", [])[:6]],
    }


def _eksik_alanlari_bul(hazir):
    """Kullanıcıya sorulabilecek gerçek zorunlu alanları okunur biçimde döndürür."""
    sonuc = []
    firma_key = hazir.get("firma_key")
    for sira, kayit in enumerate(hazir.get("veri", {}).get("kayitlar", []), 1):
        gerekli = [
            ("Tarih", "işlem tarihi"), ("Faaliyet", "faaliyet"),
            ("Cari_Tipi", "cari tipi"), ("Cari", "cari/kişi"),
            ("Islem_Turu", "işlem türü"), ("Tutar", "tutar"),
            ("PB", "para birimi"), ("Kur", "kur"),
        ]
        if firma_key == "gursar_mimarlik":
            gerekli.insert(2, ("Proje", "proje"))
        islem = sohbet_norm(kayit.get("Islem_Turu"))
        nakit_islemi = any(x in islem for x in (
            "tahsilat", "odemesi", "banka masraf", "kredi kullanimi",
            "cek teminatli borclanma", "intercompany",
        ))
        if nakit_islemi:
            gerekli.append(("Hesap_Kasa", "paranın çıktığı/girdiği hesap veya kasa"))
        for alan, etiket in gerekli:
            if kayit.get(alan) in (None, "", "None"):
                metin = f"{sira}. kayıt: {etiket}"
                if metin not in sonuc:
                    sonuc.append(metin)
    return sonuc


def ozet(token):
    veri = _BEKLEYEN.get(token)
    if not veri:
        raise ValueError("Belge kuyruğunda bulunamadı.")
    hazir = veri["hazir"]
    dogrulama = hazir.get("dogrulama") or {}
    return {
        "token": token, "durum": veri["durum"], "olusturma": veri["olusturma"],
        "transaction_id": veri.get("transaction_id") or hazir.get("_transaction_id"),
        "status": veri.get("transaction_status") or veri.get("durum") or hazir.get("_transaction_status"),
        "committed": veri.get("durum") == "TAMAMLANDI",
        "recovery_required": veri.get("durum") in {"PENDING_RECOVERY", "NEEDS_REVIEW"},
        "kaynak_turu": veri.get("kaynak_turu", "belge"),
        "onay": veri.get("onay"),
        "belge_id": hazir.get("belge_id"), "dosya": Path(str(hazir.get("foto_yolu") or "Belge")).name,
        "firma_key": hazir.get("firma_key"), "firma": FIRMALAR.get(hazir.get("firma_key"), {}).get("ad", ""),
        "tur": hazir.get("tur"), "motor": hazir.get("cikarim_motoru", "qwen2.5vl"),
        "dogrulama": {
            "durum": dogrulama.get("durum"), "skor": dogrulama.get("skor", 0),
            "uyarilar": [_uyari_ozeti(x) for x in dogrulama.get("uyarilar", [])[:8] if _uyari_ozeti(x)],
            "hatalar": [_uyari_ozeti(x) for x in dogrulama.get("hatalar", [])[:8] if _uyari_ozeti(x)],
        },
        "eksik_alanlar": _eksik_alanlari_bul(hazir),
        "kayitlar": [_kayit_ozeti(x) for x in hazir.get("veri", {}).get("kayitlar", [])[:20]],
    }


def _taslak_kimligi(item):
    """Return a stable retry identity for queue entries, when available.

    A repeated CFO request can leave an older pending draft beside the already
    committed draft.  The source hash and normalized command together identify
    a retry of the same document; a same-value but genuinely separate payment
    normally has a different source hash or command and remains visible.
    """
    hazir = item.get("hazir") or {}
    kaynak_hash = str(hazir.get("belge_hash") or "").strip()
    komut = str(hazir.get("normalize_komut") or "").strip()
    firma = str(hazir.get("firma_key") or "").strip().casefold()
    if not kaynak_hash or not komut or not firma:
        return None
    return kaynak_hash, komut, firma


def listele(kaynak_turu="belge", aktor=None, faaliyetler=None, yonetici=False):
    """List queue items subject to owner/activity scope filtering.

    The historical no-argument call remains an internal full list. HTTP callers
    must pass the authenticated actor and scope; only CFO/OWNER callers may
    intentionally request the complete queue.
    """
    aktor_norm = str(aktor or "").strip().casefold()
    kapsam = {str(x).strip().casefold() for x in (faaliyetler or [])}
    with _LOCK:
        tamamlanan = {
            kimlik
            for item in _BEKLEYEN.values()
            if item.get("durum") == "TAMAMLANDI"
            for kimlik in [_taslak_kimligi(item)]
            if kimlik is not None
        }
        sonuc = []
        for token, item in _BEKLEYEN.items():
            if kaynak_turu is not None and item.get("kaynak_turu", "belge") != kaynak_turu:
                continue
            # Do not keep showing a retry of a document that has already been
            # committed.  The queue record is preserved for audit/review; only
            # the user-facing pending list is de-duplicated.
            if item.get("durum") == "ONAY_BEKLIYOR" and _taslak_kimligi(item) in tamamlanan:
                continue
            hazir = item.get("hazir") or {}
            firma = str(hazir.get("firma_key") or "").strip().casefold()
            if not yonetici and aktor_norm and str(item.get("aktor") or "").strip().casefold() != aktor_norm:
                continue
            if kapsam and "*" not in kapsam and firma not in kapsam:
                continue
            sonuc.append(ozet(token))
    return sorted(sonuc, key=lambda x: x["olusturma"], reverse=True)


def belge_erisim_izinli(token, aktor, *, yonetici=False, faaliyetler=None):
    """Check queue ownership and activity scope before mutating a draft."""
    with _LOCK:
        item = _BEKLEYEN.get(str(token or ""))
        if not item:
            return False
        kapsam = {str(x).strip().casefold() for x in (faaliyetler or [])}
        firma = str((item.get("hazir") or {}).get("firma_key") or "").strip().casefold()
        if kapsam and "*" not in kapsam and firma not in kapsam:
            return False
        if yonetici:
            return True
        return str(item.get("aktor") or "").strip().casefold() == str(aktor or "").strip().casefold()


def sohbet_taslagi_duzelt(token, duzeltme, aktor):
    duzeltme = str(duzeltme or "").strip()
    if not duzeltme:
        raise ValueError("Düzeltmek istediğiniz bilgiyi yazın.")
    with _LOCK:
        bekleyen = _BEKLEYEN.get(token)
        if not bekleyen or bekleyen["durum"] != "ONAY_BEKLIYOR" or bekleyen.get("kaynak_turu") != "sohbet":
            raise ValueError("Düzeltmeye açık sohbet taslağı bulunamadı.")
        onceki = bekleyen["hazir"]
        kayitlar = onceki.get("veri", {}).get("kayitlar", [])
        if not kayitlar:
            raise ValueError("Düzeltilecek kayıt yok.")
        yeni = taslak_hazirla(duzeltme, onceki["firma_key"], onceki_kayit=kayitlar)
        onay_kararlari_temizle(onceki.get("belge_id"), aktor, "sohbet_taslagi_duzeltildi")
        bekleyen["hazir"] = _json_uyumlu(yeni)
        bekleyen.pop("onay", None)
        bekleyen["duzeltme"] = datetime.now().isoformat(timespec="seconds")
        _kaydet()
    audit_yaz("ui_sohbet_duzeltme", belge_id=yeni["belge_id"], aktor=str(aktor), durum=yeni["dogrulama"]["durum"])
    return ozet(token)


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
        onay_kararlari_temizle(hazir.get("belge_id"), aktor, "belge_taslagi_duzeltildi")
        bekleyen.pop("onay", None)
        dogrulama = deterministik_dogrula(hazir["veri"], hazir["firma_key"], hazir["tur"])
        hazir["dogrulama"] = dogrulama
        for kayit in kayitlar:
            kayit["_fingerprint"] = finansal_fingerprint(kayit, hazir["firma_key"], hazir["tur"])
            kayit["_dogrulama_durumu"] = dogrulama["durum"]
        _kaydet()
    audit_yaz("ui_belge_duzeltme", belge_id=hazir["belge_id"], aktor=str(aktor), alanlar=list(temiz), durum=dogrulama["durum"])
    return ozet(token)


def onayla(token, aktor, parola=None):
    _onay_parolasini_dogrula(parola, aktor)
    with _LOCK:
        bekleyen = _BEKLEYEN.get(token)
        if not bekleyen or bekleyen["durum"] != "ONAY_BEKLIYOR":
            raise ValueError("Belge onay beklemiyor.")
        hazir = bekleyen["hazir"]
        if hazir.get("cikarim_motoru") == "deterministik_cfo":
            # Sihirbaz taslagi: onay anindaki guncel master veriyle yeniden dogrulanir (LLM/metin yorumu yok).
            md = master_data_oku(hazir["firma_key"])
            hazir["dogrulama"] = deterministik_dogrula(hazir["veri"], hazir["firma_key"], hazir["tur"], md=md)
            for kayit in hazir["veri"].get("kayitlar", []):
                kayit["_dogrulama_durumu"] = hazir["dogrulama"]["durum"]
            bekleyen["hazir"] = _json_uyumlu(hazir)
            _kaydet()
        elif bekleyen.get("kaynak_turu") == "sohbet" and hazir.get("kaynak_metin"):
            md = master_data_oku(hazir["firma_key"])
            yeniden = _adayi_dogrula(hazir.get("veri") or {}, hazir["kaynak_metin"], hazir["firma_key"], md)
            if yeniden:
                hazir["veri"], hazir["dogrulama"] = yeniden
                for kayit in hazir["veri"].get("kayitlar", []):
                    kayit["_fingerprint"] = finansal_fingerprint(kayit, hazir["firma_key"], hazir["tur"])
                    kayit["_dogrulama_durumu"] = hazir["dogrulama"]["durum"]
                    kayit["_dogrulama_ozeti"] = "; ".join(
                        (hazir["dogrulama"].get("hatalar", []) + hazir["dogrulama"].get("uyarilar", []))[:3]
                    )
                bekleyen["hazir"] = _json_uyumlu(hazir)
                _kaydet()
        if hazir.get("dogrulama", {}).get("durum") == FAIL:
            raise ValueError("FAIL durumundaki belge onaylanamaz.")
        onay_kararlari_temizle(hazir.get("belge_id"), aktor, "parolali_tek_onay")
        bekleyen.pop("onay", None)
        basarili, mesaj = hazirlanan_belgeyi_yaz(hazir, onay_kaynagi=f"ui:{aktor}")
        if not basarili:
            status = str(hazir.get("_transaction_status") or "NEEDS_REVIEW")
            if status not in {"PENDING_RECOVERY", "NEEDS_REVIEW", "ABORTED"}:
                status = "NEEDS_REVIEW"
            retryable_lock = status == "PENDING_RECOVERY" and any(
                kelime in (mesaj or "").casefold() for kelime in ("excel", "kilitli", "onedrive")
            )
            if retryable_lock:
                bekleyen["transaction_id"] = hazir.get("_transaction_id")
                bekleyen["transaction_status"] = status
                bekleyen["hata"] = mesaj or status
                _kaydet()
                return {
                    "tamam": False,
                    "committed": False,
                    "status": status,
                    "recovery_required": True,
                    "retryable": True,
                    "transaction_id": hazir.get("_transaction_id"),
                    "mesaj": mesaj or status,
                    "belge": ozet(token),
                }
            bekleyen["durum"] = status
            bekleyen["transaction_id"] = hazir.get("_transaction_id")
            bekleyen["hata"] = mesaj or status
            bekleyen["tamamlanma"] = datetime.now().isoformat(timespec="seconds")
            _kaydet()
            return {
                "tamam": False,
                "committed": False,
                "status": status,
                "recovery_required": status == "PENDING_RECOVERY",
                "transaction_id": hazir.get("_transaction_id"),
                "mesaj": mesaj or status,
                "belge": ozet(token),
            }
        bekleyen["durum"] = "TAMAMLANDI"
        bekleyen["tamamlanma"] = datetime.now().isoformat(timespec="seconds")
        _kaydet()
    try:
        import cfo_sohbet

        cfo_sohbet.onay_tamamlandi(token)
    except Exception as exc:  # taslak durumu yazilamamasi finansal kaydi geri almaz
        audit_yaz("cfo_taslak_durum_hatasi", token=token, hata=str(exc))
    return {"tamam": True, "onay": {"tamam": True, "yontem": "parola"}, "belge": ozet(token)}


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
    try:
        import cfo_sohbet

        cfo_sohbet.reddedildi(token)
    except Exception as exc:
        audit_yaz("cfo_taslak_durum_hatasi", token=token, hata=str(exc))
    return ozet(token)


_yukle()
