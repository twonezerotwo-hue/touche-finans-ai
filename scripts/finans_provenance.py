"""Dashboard metrikleri icin aciklanabilir kanit/provenance katmani.

Bu modul hesaplama motorunun yerine gecmez. ``proje_finansi`` tarafindan
hesaplanan deterministik katkilarin; kapsam, formul, kaynak fis ve mutabakat
durumuyla birlikte guvenli bir sozlesmeye donusturulmesini saglar.
Kullanicidan gelen metin hicbir zaman SQL veya kaynak kimligi olarak calistirilmaz.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import proje_finansi as pf

DNA = "NO_EVIDENCE"
METRIC_SPECS = {
    "revenue": ("gelir", "Kazanılan gelir", "Gelir = gelir tahakkuku satırları − ters kayıtlar"),
    "expenses": ("gider", "Gider", "Gider = gider tahakkuku satırları − ters kayıtlar"),
    "cash_balance": ("nakit_bakiye", "Nakit bakiyesi", "Nakit bakiyesi = kasa/banka borç bakiyesi − alacak bakiyesi"),
    "receivables": ("kalan_alacak", "Kalan alacak", "Kalan alacak = 120/131/159 borç bakiyesi"),
    "payables": ("acik_borc", "Açık borç", "Açık borç = 300/320/331/335/336 alacak bakiyesi"),
    "profit": ("kar", "Kâr / zarar", "Kâr / zarar = kazanılan gelir − gider"),
    "payments": ("odenen", "Ödenen", "Ödeme = kasa/banka çıkışı, grup içi kayıtlar hariç"),
    "collections": ("tahsilat", "Tahsil edilen", "Tahsilat = alacağı kapatan nakit girişi"),
    "net_cash": ("net_nakit", "Net nakit", "Net nakit = tahsilat + doğrudan nakit gelir − ödeme"),
    "loans": ("finansman", "Finansman bakiyesi", "Finansman = 300/331/336 hesaplarının net bakiyesi"),
}
ALIASES = {
    "gelir": "revenue", "kazanilan_gelir": "revenue", "gider": "expenses", "maliyet": "expenses",
    "nakit": "cash_balance", "nakit_bakiye": "cash_balance", "alacak": "receivables",
    "borc": "payables", "acik_borc": "payables", "kar": "profit", "karlilik": "profit",
    "odeme": "payments", "odenen": "payments", "tahsilat": "collections", "tahsil": "collections",
    "net_nakit": "net_cash", "kredi": "loans", "loan": "loans",
}
_NAKIT = tuple(pf.NAKIT)
_FINANS = ("300", "331", "336")

_PARA_TOKENS = "₺|TL|TRY|USD|EUR|USDT|GBP"
_PARA_RE = re.compile(
    rf"(?P<sign>-)?(?P<sayi>\d[\d.\s]*(?:,\d{{1,2}}|\.\d{{1,2}})?)\s*(?P<para>{_PARA_TOKENS})\b",
    re.IGNORECASE,
)
_KANIT_ID_RE = re.compile(
    r"\b(?:JRN|TX|DOC|BELGE|KRD|ODM|MAS|FAT|INV)[-_][A-Z0-9][A-Z0-9_-]{2,}\b",
    re.IGNORECASE,
)
_PARA_ALANLARI = (
    "tutar", "amount", "toplam", "bakiye", "nakit", "alacak", "borc", "borç",
    "odeme", "ödeme", "tahsil", "gelir", "gider", "kar", "kâr", "maliyet",
    "finans", "kredi", "fiyat", "ciro", "net", "rezerv", "kapasite", "acik",
)


def normalize_metric(metric: str) -> str:
    key = str(metric or "").strip().casefold().replace("-", "_").replace(" ", "_")
    key = ALIASES.get(key, key)
    if key not in METRIC_SPECS:
        raise ValueError("Desteklenmeyen metrik")
    return key


def _decimal_from_text(value: str) -> Decimal | None:
    """Türkçe/uluslararası para yazımını kuruşa yuvarlanmış Decimal'e çevirir."""
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return None
    try:
        if "," in text and "." in text:
            # Son ayraç ondalıktır: 1.234.567,89 veya 1,234,567.89.
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif text.count(".") > 1:
            text = text.replace(".", "")
        return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _iter_financial_numbers(value: Any, key: str = ""):
    """Kanıt paketinden yalnızca finansal alanları güvenli biçimde çıkarır."""
    normalized = str(key or "").casefold()
    finans_alani = any(token in normalized for token in _PARA_ALANLARI)
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _iter_financial_numbers(child, str(child_key))
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _iter_financial_numbers(child, key)
        return
    if finans_alani and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
        if "kurus" in normalized:
            number /= Decimal(100)
        yield number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _iter_ids(value: Any, key: str = ""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _iter_ids(child, str(child_key))
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _iter_ids(child, key)
        return
    if isinstance(value, str):
        # Kimlikler yapılandırılmış alanlarda veya metin kanıtlarında olabilir.
        yield from _KANIT_ID_RE.findall(value)


def validate_llm_output(answer: Any, claims: Any, evidence: Any) -> dict[str, Any]:
    """LLM açıklamasını yalnızca önceden yetkilendirilmiş kanıtla sınar.

    Bu fonksiyon hesap yapmaz ve cevabı düzeltmez. Para birimli bir iddia,
    kanıt paketindeki bir tutarla eşleşmiyorsa veya bilinmeyen bir voucher/
    belge kimliği kullanıyorsa ``valid=False`` döner; çağıran deterministik
    cevaba geri dönmelidir.
    """
    allowed_amounts = set(_iter_financial_numbers(evidence))
    allowed_ids = {item.casefold() for item in _iter_ids(evidence)}
    text_parts = [str(answer or "")]
    if isinstance(claims, (list, tuple, set)):
        text_parts.extend(str(item) for item in claims)
    elif claims:
        text_parts.append(str(claims))
    text = "\n".join(text_parts)
    unsupported_amounts: list[str] = []
    for match in _PARA_RE.finditer(text):
        parsed = _decimal_from_text(match.group("sayi"))
        if parsed is not None and parsed not in allowed_amounts:
            unsupported_amounts.append(match.group(0).strip())
    unsupported_ids = [
        item for item in _KANIT_ID_RE.findall(text)
        if item.casefold() not in allowed_ids
    ]
    if unsupported_amounts or unsupported_ids:
        reasons = []
        if unsupported_amounts:
            reasons.append("desteksiz tutar: " + ", ".join(unsupported_amounts[:3]))
        if unsupported_ids:
            reasons.append("desteksiz kanıt kimliği: " + ", ".join(unsupported_ids[:3]))
        return {
            "valid": False,
            "reason": "; ".join(reasons),
            "unsupported_amounts": unsupported_amounts,
            "unsupported_ids": unsupported_ids,
        }
    return {"valid": True, "reason": "", "unsupported_amounts": [], "unsupported_ids": []}


def _scope_filter(fisler: Iterable[dict], faaliyet=None, proje=None, bas=None, bit=None):
    result = []
    for f in fisler:
        if faaliyet and f.get("faaliyet") != faaliyet:
            continue
        if proje and str(f.get("proje") or "").casefold() != str(proje).casefold():
            continue
        if bas and str(f.get("tarih") or "") < str(bas):
            continue
        if bit and str(f.get("tarih") or "") > str(bit):
            continue
        if str(f.get("durum") or "").upper() in {"IPTAL", "İPTAL", "REVERSED"}:
            continue
        result.append(f)
    return result


def _cash_balance(f):
    return sum(b - a for kod, _tip, b, a in f.get("satirlar", []) if kod in _NAKIT)


def _loan_balance(f):
    return sum(a - b for kod, _tip, b, a in f.get("satirlar", []) if kod in _FINANS)


def _contribution(f, metric):
    k = pf._katki(f.get("satirlar", []))
    if metric == "cash_balance":
        return _cash_balance(f)
    if metric == "loans":
        return _loan_balance(f)
    return int(k.get(METRIC_SPECS[metric][0], 0))


def _currency_map(fisler):
    """Ledger tutarlarini TL'ye normalize ettigi icin para birimi kanitini korur."""
    result: dict[str, dict[str, Any]] = {}
    with pf.okuma_baglantisi() as con:
        if con is None:
            return result
        try:
            for row in con.execute("SELECT fis_id, para_birimi, kur_mikro FROM fisler"):
                result[row["fis_id"]] = {
                    "currency": row["para_birimi"] or "TL",
                    "rate": (row["kur_mikro"] or 1000000) / 1000000,
                }
        except sqlite3.Error:
            return result
    return result


def _status(comparison=None, freshness=None):
    if comparison:
        sqlite_value = comparison.get("sqlite_total_kurus")
        excel_value = comparison.get("excel_total_kurus")
        if sqlite_value is not None and excel_value is not None and int(sqlite_value) != int(excel_value):
            return "INCONSISTENT", {"difference_kurus": int(excel_value) - int(sqlite_value)}
        if comparison.get("status") in {"INCONSISTENT", "PARTIALLY_VERIFIED", "NO_EVIDENCE"}:
            return comparison["status"], comparison
    if freshness and freshness.get("stale"):
        return "STALE", {"last_verified": freshness.get("last_verified")}
    return "CONSISTENT", {}


def metric_provenance(
    metric: str,
    fisler: dict,
    *,
    faaliyet: str | None = None,
    proje: str | None = None,
    bas: str | None = None,
    bit: str | None = None,
    page: int = 1,
    page_size: int = 25,
    comparison: dict | None = None,
    freshness: dict | None = None,
    calculation_at: str | None = None,
) -> dict[str, Any]:
    """Tek bir dashboard metrigi icin JSON uyumlu provenance sozlesmesi."""
    key = normalize_metric(metric)
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 25)))
    secili = _scope_filter(fisler.values() if isinstance(fisler, dict) else fisler, faaliyet, proje, bas, bit)
    verified = [f for f in secili if str(f.get("durum") or "").upper() in {"MUHASEBELESTI", "GERCEKLESTI", "GERÇEKLEŞTİ", "AKTIF", ""}]
    ham = pf.hesapla(verified)
    totals = {"gelir": ham["gelir"], "gider": ham["gider"], "kalan_alacak": ham["kalan_alacak"],
              "acik_borc": ham["acik_borc"], "kar": ham["kar"], "odenen": ham["odenen"],
              "tahsilat": ham["tahsilat"], "net_nakit": ham["net_nakit"],
              "nakit_bakiye": sum(_cash_balance(f) for f in verified),
              "finansman": sum(_loan_balance(f) for f in verified)}
    total = int(totals[METRIC_SPECS[key][0]])
    sources = []
    currencies = _currency_map(verified)
    for f in sorted(verified, key=lambda x: (str(x.get("tarih") or ""), str(x.get("fis_id") or ""))):
        contribution = int(_contribution(f, key))
        if not contribution:
            continue
        cur = currencies.get(f.get("fis_id"), {"currency": "TL", "rate": 1})
        sources.append({
            "transaction_id": f.get("fis_id"), "voucher_id": f.get("fis_id"),
            "excel_record_id": f.get("belge_id") or None, "date": f.get("tarih"),
            "activity": f.get("faaliyet"), "project": f.get("proje") or None,
            "counterparty": f.get("cari") or None, "description": (f.get("aciklama") or "")[:240],
            "amount_kurus": contribution, "currency": cur["currency"],
            "conversion": {"rate": cur["rate"], "target_currency": "TL", "normalized": True},
        })
    status, status_detail = _status(comparison, freshness)
    start = (page - 1) * page_size
    shown = sources[start:start + page_size]
    now = calculation_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {
        "metric_id": key, "metric_name": METRIC_SPECS[key][1], "value_kurus": total,
        "currency": "TL", "period": {"start": bas, "end": bit},
        "scope": {"activity": faaliyet, "project": proje}, "formula": METRIC_SPECS[key][2],
        "source_transactions": shown, "source_count": len(sources),
        "pagination": {"page": page, "page_size": page_size, "total": len(sources),
                        "pages": (len(sources) + page_size - 1) // page_size},
        "calculation_timestamp": now, "reconciliation_status": status,
        "reconciliation": status_detail, "freshness": freshness or {"status": "CURRENT"},
        "evidence": "LEDGER" if sources else DNA,
    }
    if status in {"INCONSISTENT", "STALE", "PARTIALLY_VERIFIED", "NO_EVIDENCE"}:
        result["warning"] = {
            "INCONSISTENT": "Excel ve merkezi defter toplamları uyuşmuyor; otomatik düzeltme yapılmadı.",
            "STALE": "Kanıt verisi güncel değil; yeniden doğrulama gerekir.",
            "PARTIALLY_VERIFIED": "Metrik kısmen doğrulanabildi; eksik kanıt var.",
            "NO_EVIDENCE": "Bu metrik için güvenilir kaynak kaydı bulunamadı.",
        }[status]
    return result


def from_database(metric: str, **kwargs) -> dict[str, Any]:
    """Salt-okunur merkezi defterden provenance üretir."""
    with pf.okuma_baglantisi() as con:
        fisler, _ = pf._satirlari_oku(con, kwargs.pop("faaliyet", None))
    return metric_provenance(metric, fisler, **kwargs)

