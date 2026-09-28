# -*- coding: utf-8 -*-
"""Belge butunlugu, finansal parmak izi ve denetim kaydi yardimcilari."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from config import AUDIT_LOG, LOGLAR

log = logging.getLogger(__name__)

FINGERPRINT_ALANLARI = (
    "Faaliyet",
    "Proje",
    "Cari_Tipi",
    "Cari",
    "Alici",
    "Islem_Turu",
    "Tarih",
    "Tutar",
    "Tutar_TL",
    "PB",
    "Belge_No",
    "Fatura_No",
    "Irsaliye_No",
    "FAST_Sorgu_No",
)


def belge_sha256(dosya_yolu: str | Path) -> str:
    """Dosyayi parca parca okuyarak tam SHA-256 ozeti dondurur."""
    h = hashlib.sha256()
    with Path(dosya_yolu).open("rb") as dosya:
        for parca in iter(lambda: dosya.read(1024 * 1024), b""):
            h.update(parca)
    return h.hexdigest()


def _metin_normalize(deger) -> str:
    if deger is None:
        return ""
    if isinstance(deger, (datetime, date)):
        return deger.isoformat()
    metin = unicodedata.normalize("NFKC", str(deger)).strip().casefold()
    return re.sub(r"\s+", " ", metin)


def _tutar_normalize(deger) -> str:
    if deger in (None, ""):
        return ""
    metin = str(deger).strip().replace(" ", "")
    if "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        return format(Decimal(metin).normalize(), "f")
    except (InvalidOperation, ValueError):
        return _metin_normalize(deger)


def finansal_fingerprint(kayit: dict, firma_key: str = "", belge_turu: str = "") -> str:
    """Finansal olarak ayni kaydi sabit bir SHA-256 parmak izine donusturur."""
    kanonik = {"firma": firma_key, "belge_turu": belge_turu}
    for alan in FINGERPRINT_ALANLARI:
        deger = kayit.get(alan)
        if alan in {"Tutar", "Tutar_TL"}:
            kanonik[alan] = _tutar_normalize(deger)
        else:
            kanonik[alan] = _metin_normalize(deger)
    ham = json.dumps(kanonik, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def audit_yaz(olay: str, **alanlar) -> None:
    """Tek satirlik JSON denetim kaydi yazar; kayit hatasi ana akisi durdurmaz."""
    try:
        LOGLAR.mkdir(parents=True, exist_ok=True)
        kayit = {
            "zaman": datetime.now().astimezone().isoformat(timespec="seconds"),
            "olay": olay,
            "pid": os.getpid(),
            **alanlar,
        }
        with Path(AUDIT_LOG).open("a", encoding="utf-8") as dosya:
            dosya.write(json.dumps(kayit, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # audit arizasi belgeyi kaybettirmemeli
        log.error("Audit log yazilamadi: %s", exc)
