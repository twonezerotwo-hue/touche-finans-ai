"""CFO komut sözleşmesi ve güvenli çoklu işlem çözümleme katmanı.

Bu modül yalnızca yorumlar ve doğrulama çıktısı üretir; Excel, SQLite veya
onay kuyruğuna yazmaz. LLM çıktısı kullanılırsa bile finansal yürütme katmanına
geçmeden önce bu sözleşmeden geçirilmelidir.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import cfo_niyet as N
from cfo_varliklar import Katalog

READ_ONLY_QUERY = "READ_ONLY_QUERY"
FINANCIAL_DRAFT_REQUEST = "FINANCIAL_DRAFT_REQUEST"
CORRECTION_REQUEST = "CORRECTION_REQUEST"
CANCELLATION_REQUEST = "CANCELLATION_REQUEST"
REPORT_REQUEST = "REPORT_REQUEST"
CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


@dataclass(frozen=True)
class TransactionProposal:
    """Bir işlem için yalnızca doğrulama/önizleme verisi."""

    source_text: str
    transaction_type: str | None
    activity: str | None
    project: str | None
    counterparty: str | None
    amount: str | None
    currency: str | None
    transaction_date: str | None
    due_date: str | None
    confidence: str
    missing_fields: tuple[str, ...]
    validation_errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntelligenceResult:
    intent: str
    proposals: tuple[TransactionProposal, ...] = ()
    clarification: tuple[str, ...] = ()
    reason: str = ""
    safe_to_draft: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "proposals": [x.as_dict() for x in self.proposals],
            "clarification": list(self.clarification),
            "reason": self.reason,
            "safe_to_draft": self.safe_to_draft,
        }


_PART_RE = re.compile(
    r"\s*(?:;|\n+|,\s*(?=[A-Za-zÇĞİÖŞÜçğıöşü])|\s+ve\s+(?=[A-Za-zÇĞİÖŞÜçğıöşü][^,;]{0,90}\d))\s*"
)


def _parcalara_ayir(metin: str) -> list[str]:
    """Ondalık virgülü bölmeden, işlem cümlelerini ayırır."""
    sonuc = [x.strip(" .") for x in _PART_RE.split(str(metin or "")) if x.strip(" .")]
    return sonuc or [str(metin or "").strip()]


def _eksikler(n: N.Niyet) -> list[str]:
    eksik: list[str] = []
    if not n.tutar:
        eksik.append("Tutar")
    if not n.pb:
        eksik.append("Para birimi")
    if not n.islem:
        eksik.append("İşlem türü")
    if not n.faaliyet:
        eksik.append("Faaliyet")
    if not n.cari:
        eksik.append("Cari")
    return eksik


def _proposal(n: N.Niyet, text: str) -> TransactionProposal:
    eksik = _eksikler(n)
    hatalar: list[str] = []
    if n.desteklenmeyen:
        hatalar.append(n.desteklenmeyen)
    if n.tarih_hatasi:
        hatalar.append(n.tarih_hatasi)
    if n.belirsiz:
        hatalar.append("Bir veya daha fazla alan birden fazla kayda eşleşiyor.")
    if n.oneri:
        hatalar.append("Yazım benzerliği bulunan alanlar kullanıcı onayı bekliyor.")
    return TransactionProposal(
        source_text=text,
        transaction_type=n.islem,
        activity=n.faaliyet.gorunen() if n.faaliyet else None,
        project=n.proje.ad if n.proje else None,
        counterparty=n.cari.ad if n.cari else n.yeni_cari_adi or None,
        amount=str(n.tutar) if n.tutar is not None else None,
        currency=n.pb,
        transaction_date=n.tarih.isoformat() if n.tarih else None,
        due_date=None,
        confidence="high" if not eksik and not hatalar else "needs_review",
        missing_fields=tuple(eksik),
        validation_errors=tuple(hatalar),
    )


def classify_intent(text: str, n: N.Niyet | None = None) -> str:
    """Komutun okuma mı yazma mı olduğunu güvenli biçimde sınıflandırır."""
    sade = N.norm(text)
    if re.search(r"\b(?:iptal|silme|geri al|ters kayit)\b", sade) or "iptal et" in sade:
        return CANCELLATION_REQUEST
    if any(x in sade for x in ("duzelt", "yanlis kayit", "yanlis yazildi", "duzeltme")):
        return CORRECTION_REQUEST
    if n is not None and n.desteklenmeyen:
        return UNSUPPORTED_REQUEST
    if re.search(r"\b(?:transfer|havale et|aktar)\b", sade) and not re.search(r"\b(?:geldi|girdi|yatti|gonderildi)\b", sade):
        return UNSUPPORTED_REQUEST
    if any(x in sade for x in ("rapor", "ozet", "listele", "kalem kalem", "karsilastir")):
        return REPORT_REQUEST if n is None or n.tur != "KAYIT" else FINANCIAL_DRAFT_REQUEST
    if n is not None and n.tur == "SORGU":
        return READ_ONLY_QUERY
    if n is not None and n.tur == "KAYIT":
        return FINANCIAL_DRAFT_REQUEST
    if any(x in sade for x in ("ne kadar", "nedir", "var mi", "goster", "göster", "durumu")):
        return READ_ONLY_QUERY
    return CLARIFICATION_REQUIRED


def analyze(text: str, katalog: Katalog, bugun: date | None = None) -> IntelligenceResult:
    """Mesajı yapısal taslaklara ayırır; hiçbir kalıcı yazma yapmaz."""
    ham = str(text or "").strip()
    if not ham:
        return IntelligenceResult(CLARIFICATION_REQUIRED, clarification=("İşlem veya sorunuzu yazın.",))
    ilk = N.analiz_et(ham, katalog, bugun)
    intent = classify_intent(ham, ilk)
    if intent in {READ_ONLY_QUERY, REPORT_REQUEST, CANCELLATION_REQUEST, CORRECTION_REQUEST, UNSUPPORTED_REQUEST}:
        return IntelligenceResult(intent, reason="Mevcut güvenli akışa yönlendirilmeli.")

    parçalar = _parcalara_ayir(ham)
    if len(parçalar) == 1:
        p = _proposal(ilk, ham)
        if p.validation_errors or p.missing_fields:
            return IntelligenceResult(CLARIFICATION_REQUIRED, (p,), tuple(p.missing_fields), "Eksik veya belirsiz alan var.")
        return IntelligenceResult(FINANCIAL_DRAFT_REQUEST, (p,), safe_to_draft=True)

    proposals: list[TransactionProposal] = []
    bağlam = ilk
    for parça in parçalar:
        n = N.analiz_et(parça, katalog, bugun, zorla=True)
        # Mesajın başındaki faaliyet/proje/tarih bağlamı sonraki bağımsız
        # cümlelere taşınabilir; cari ve tutar asla sessizce taşınmaz.
        for alan in ("faaliyet", "proje", "tarih", "tarih_kaynagi", "pb"):
            if getattr(n, alan, None) in (None, "") and getattr(bağlam, alan, None) not in (None, ""):
                setattr(n, alan, getattr(bağlam, alan))
        for alan in ("faaliyet", "proje", "tarih", "tarih_kaynagi", "pb"):
            if getattr(n, alan, None) not in (None, ""):
                setattr(bağlam, alan, getattr(n, alan))
        proposals.append(_proposal(n, parça))
    eksikler = sorted({x for p in proposals for x in p.missing_fields})
    hatalı = any(p.validation_errors for p in proposals)
    if eksikler or hatalı:
        return IntelligenceResult(CLARIFICATION_REQUIRED, tuple(proposals), tuple(eksikler), "Çoklu işlem taslağında doğrulama gerekiyor.")
    return IntelligenceResult(FINANCIAL_DRAFT_REQUEST, tuple(proposals), reason="Bağımsız onaylanacak çoklu işlem taslağı.")

