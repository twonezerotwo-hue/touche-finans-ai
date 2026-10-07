# -*- coding: utf-8 -*-
"""Belge otomasyon hatti.

UPLOAD -> PARSE -> OCR -> FIELD EXTRACTION -> CLASSIFICATION -> DUPLICATE CHECK
-> MATCHING -> USER REVIEW -> APPROVAL -> POSTING adimlarini temsil eder.

Deterministik/belge tarafi once calisir. OCR/AI yalnizca oneri uretir; otomatik
muhasebe kaydi OLUSTURMAZ. Belge metni guvenilmeyen veri olarak ele alinir;
icerigindeki talimatlar komut olarak yorumlanmaz.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime

from veri_katmani import baglanti, veritabani_hazirla

ASAMALAR = ["UPLOAD", "PARSE", "OCR", "FIELD_EXTRACTION", "CLASSIFICATION",
            "DUPLICATE_CHECK", "MATCHING", "USER_REVIEW", "APPROVAL", "POSTING"]

TUR_ANAHTARLARI = {
    "invoice": ["fatura", "invoice", "e-fatura", "efatura"],
    "receipt": ["makbuz", "fiş", "fis", "receipt"],
    "bank_statement": ["hesap özeti", "hesap ozeti", "ekstre", "bank statement", "dekont"],
    "payment_receipt": ["ödeme dekontu", "odeme dekontu", "fast", "eft", "havale"],
    "contract": ["sözleşme", "sozlesme", "protokol", "contract"],
    "progress_payment": ["hakediş", "hakedis", "progress payment", "metraj"],
    "offer": ["teklif", "proforma", "offer", "quote"],
    "purchase_order": ["sipariş", "siparis", "purchase order"],
    "expense": ["masraf", "gider", "expense"],
    "other": [],
}
TUR_ETIKET = {
    "invoice": "Fatura", "receipt": "Makbuz", "bank_statement": "Banka ekstresi",
    "payment_receipt": "Ödeme dekontu", "contract": "Sözleşme",
    "progress_payment": "Hakediş", "offer": "Teklif", "purchase_order": "Sipariş",
    "expense": "Masraf belgesi", "other": "Diğer",
}

_TUTAR_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:[.,]\d{2})?)")
_TARIH_RE = re.compile(r"(\d{2}[.]\d{2}[.]\d{4})")
_FATURA_RE = re.compile(r"(?:fatura|invoice)\s*(?:no|no\.|numaras[ıi])?\s*[:#]?\s*([A-Za-z0-9\-/]{4,20})", re.I)
_VKN_RE = re.compile(r"\b(\d{10,11})\b")


def _norm(metin) -> str:
    s = unicodedata.normalize("NFKD", str(metin or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("ı", "i")


def _tutar_coz(metin: str) -> float:
    metin = metin.strip().replace(" ", "")
    if "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        return round(float(metin), 2)
    except ValueError:
        return 0.0


def siniflandir(metin: str, dosya_adi: str = "") -> tuple[str, int]:
    """Belge metninden tur ve guven yuzdesi tahmin eder (deterministik)."""
    hedef = f"{_norm(dosya_adi)} {_norm(metin)}"
    puanlar = {}
    for tur, kelimeler in TUR_ANAHTARLARI.items():
        puan = sum(3 if k in _norm(dosya_adi) else 1 for k in kelimeler if k in hedef)
        if puan:
            puanlar[tur] = puan
    if not puanlar:
        return "other", 20
    tur = max(puanlar, key=puanlar.get)
    toplam = sum(puanlar.values())
    guven = int(min(97, 45 + 52 * puanlar[tur] / max(1, toplam)))
    return tur, guven


def alan_cikar(metin: str) -> dict:
    """Metinden tarih, tutar, fatura no, VKN gibi alanlari cikarir."""
    metin = str(metin or "")
    tutarlar = [_tutar_coz(x) for x in _TUTAR_RE.findall(metin)]
    tarih = _TARIH_RE.search(metin)
    fatura = _FATURA_RE.search(metin)
    vkn = _VKN_RE.search(metin)
    return {
        "tarih": tarih.group(1) if tarih else None,
        "toplam": max(tutarlar) if tutarlar else None,
        "fatura_no": fatura.group(1) if fatura else None,
        "vergi_no": vkn.group(1) if vkn else None,
        "para_birimi": "EUR" if "eur" in _norm(metin) or "€" in metin else
                       ("USD" if "usd" in _norm(metin) or "$" in metin else "TL"),
    }


def icerik_hash(dosya_yolu=None, metin: str = "") -> str:
    if dosya_yolu:
        try:
            from pathlib import Path

            h = hashlib.sha256()
            with Path(dosya_yolu).open("rb") as f:
                for parca in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(parca)
            return h.hexdigest()
        except OSError:
            pass
    return hashlib.sha256(str(metin or "").encode("utf-8")).hexdigest()


def mukerrer_kontrol(icerik_ozet: str) -> dict:
    """Ayni icerik (hash) daha once islendi mi? Duplicate detection."""
    veritabani_hazirla()
    with baglanti() as con:
        belge = con.execute("SELECT belge_id, kayit_zamani FROM belge_butunlugu WHERE sha256=?",
                            (icerik_ozet,)).fetchone()
        kuyruk = con.execute("SELECT belge_id FROM belge_kuyrugu WHERE belge_id=?",
                             (f"HASH-{icerik_ozet[:16]}",)).fetchone()
    mevcut = None
    if belge:
        mevcut = {"kaynak": "belge_butunlugu", "belge_id": belge["belge_id"], "zaman": belge["kayit_zamani"]}
    elif kuyruk:
        mevcut = {"kaynak": "belge_kuyrugu", "belge_id": kuyruk["belge_id"], "zaman": None}
    return {"mukerrer": bool(mevcut), "mevcut": mevcut}


def pipeline_calistir(metin: str = "", dosya_adi: str = "", firma=None, proje=None) -> dict:
    """Belge hattini (deterministik) calistirir; oneri+guven uretir, kayit YAPMAZ."""
    ozet = icerik_hash(dosya_yolu=dosya_adi) if dosya_adi else icerik_hash(metin=metin)
    tur, guven = siniflandir(metin, dosya_adi)
    alanlar = alan_cikar(metin)
    dup = mukerrer_kontrol(ozet)
    oneri = None
    if firma:
        oneri = {"faaliyet": firma, "proje": proje, "guven": guven,
                 "mesaj": f"Bu belge {firma}{(' / ' + proje) if proje else ''} projesine ait olabilir."}
    adimlar = [
        {"adim": "UPLOAD", "durum": "PASS"},
        {"adim": "PARSE", "durum": "PASS" if (metin or dosya_adi) else "WARNING"},
        {"adim": "OCR", "durum": "GEREKLI" if not metin else "ATLANDI"},
        {"adim": "FIELD_EXTRACTION", "durum": "PASS" if any(v for v in alanlar.values()) else "WARNING"},
        {"adim": "CLASSIFICATION", "durum": "PASS", "tur": tur, "guven": guven},
        {"adim": "DUPLICATE_CHECK", "durum": "FAIL" if dup["mukerrer"] else "PASS"},
        {"adim": "MATCHING", "durum": "PASS" if oneri else "WARNING"},
        {"adim": "USER_REVIEW", "durum": "BEKLIYOR"},
        {"adim": "APPROVAL", "durum": "BEKLIYOR"},
        {"adim": "POSTING", "durum": "BEKLIYOR"},
    ]
    return {"tur": tur, "tur_etiket": TUR_ETIKET.get(tur, "Diğer"), "guven": guven,
            "alanlar": alanlar, "icerik_hash": ozet, "mukerrer": dup, "oneri": oneri,
            "adimlar": adimlar}


def kuyruga_ekle(belge_id: str, pipeline: dict, aktor="sistem") -> dict:
    """Inceleme kuyruguna belge ekler/gunceller (muhasebe kaydi YAPMAZ)."""
    import json

    veritabani_hazirla()
    simdi = datetime.now().isoformat(timespec="seconds")
    with baglanti() as con:
        con.execute(
            "INSERT INTO belge_kuyrugu(belge_id,durum,tur,guven,eslesme,alanlar,adimlar,olusturan,"
            "olusturma,guncelleme) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(belge_id) DO UPDATE SET durum=excluded.durum,tur=excluded.tur,"
            "guven=excluded.guven,eslesme=excluded.eslesme,alanlar=excluded.alanlar,"
            "adimlar=excluded.adimlar,guncelleme=excluded.guncelleme",
            (belge_id, "USER_REVIEW", pipeline.get("tur"), pipeline.get("guven"),
             json.dumps(pipeline.get("oneri"), ensure_ascii=False),
             json.dumps(pipeline.get("alanlar"), ensure_ascii=False),
             json.dumps(pipeline.get("adimlar"), ensure_ascii=False), str(aktor), simdi, simdi),
        )
    return {"belge_id": belge_id, "durum": "USER_REVIEW"}


def kuyruk_durumu(belge_id: str) -> dict:
    import json

    veritabani_hazirla()
    with baglanti() as con:
        r = con.execute("SELECT * FROM belge_kuyrugu WHERE belge_id=?", (str(belge_id),)).fetchone()
    if not r:
        return {"hata": "Belge bulunamadi"}
    return {"belge_id": r["belge_id"], "durum": r["durum"], "tur": r["tur"], "guven": r["guven"],
            "eslesme": json.loads(r["eslesme"] or "null"), "alanlar": json.loads(r["alanlar"] or "{}"),
            "adimlar": json.loads(r["adimlar"] or "[]")}


