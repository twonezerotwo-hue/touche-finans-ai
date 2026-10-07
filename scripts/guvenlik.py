# -*- coding: utf-8 -*-
"""Belge butunlugu, finansal parmak izi ve denetim kaydi yardimcilari."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from config import AUDIT_LOG, LOGLAR

log = logging.getLogger(__name__)
_AUDIT_LOCK = threading.RLock()
_ZINCIR_BASLANGICI = "0" * 64

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
    """SHA-256 zincirli tek satırlık JSON denetim kaydı yazar."""
    try:
        with _AUDIT_LOCK:
            LOGLAR.mkdir(parents=True, exist_ok=True)
            yol = Path(AUDIT_LOG)
            onceki_hash = _son_audit_hash(yol)
            kayit = {
                "zincir_surumu": 1,
                "zaman": datetime.now().astimezone().isoformat(timespec="microseconds"),
                "olay": olay,
                "pid": os.getpid(),
                "onceki_hash": onceki_hash,
                **alanlar,
            }
            ham = json.dumps(kayit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            kayit["kayit_hash"] = hashlib.sha256(ham.encode("utf-8")).hexdigest()
            with yol.open("a", encoding="utf-8") as dosya:
                dosya.write(json.dumps(kayit, ensure_ascii=False, default=str) + "\n")
                dosya.flush()
                os.fsync(dosya.fileno())
    except Exception as exc:  # audit arizasi belgeyi kaybettirmemeli
        log.error("Audit log yazilamadi: %s", exc)


def _son_audit_hash(yol: Path) -> str:
    if not yol.exists():
        return _ZINCIR_BASLANGICI
    try:
        for satir in reversed(yol.read_text(encoding="utf-8").splitlines()):
            if not satir.strip():
                continue
            kayit = json.loads(satir)
            if kayit.get("zincir_surumu") == 1 and re.fullmatch(r"[a-f0-9]{64}", str(kayit.get("kayit_hash", ""))):
                return kayit["kayit_hash"]
    except (OSError, json.JSONDecodeError):
        pass
    return _ZINCIR_BASLANGICI


def audit_zincirini_dogrula(yol: str | Path | None = None) -> dict:
    """Legacy satırları koruyarak hash zincirinin değiştirilmediğini doğrular."""
    audit_yolu = Path(yol or AUDIT_LOG)
    if not audit_yolu.exists():
        return {"gecerli": True, "zincirli_kayit": 0, "legacy_kayit": 0, "hata_satiri": None}
    onceki, zincirli, legacy = _ZINCIR_BASLANGICI, 0, 0
    for no, satir in enumerate(audit_yolu.read_text(encoding="utf-8").splitlines(), 1):
        if not satir.strip():
            continue
        try:
            kayit = json.loads(satir)
        except json.JSONDecodeError:
            return {"gecerli": False, "zincirli_kayit": zincirli, "legacy_kayit": legacy, "hata_satiri": no}
        if kayit.get("zincir_surumu") != 1:
            legacy += 1
            continue
        verilen = str(kayit.pop("kayit_hash", ""))
        if kayit.get("onceki_hash") != onceki:
            return {"gecerli": False, "zincirli_kayit": zincirli, "legacy_kayit": legacy, "hata_satiri": no}
        ham = json.dumps(kayit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        beklenen = hashlib.sha256(ham.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(verilen, beklenen):
            return {"gecerli": False, "zincirli_kayit": zincirli, "legacy_kayit": legacy, "hata_satiri": no}
        onceki, zincirli = verilen, zincirli + 1
    return {"gecerli": True, "zincirli_kayit": zincirli, "legacy_kayit": legacy, "hata_satiri": None}
