"""Sentetik CFO değerlendirme corpus'u ve ölçüm sözleşmeleri.

Bu modül kalıcı veriye, Excel'e veya SQLite'a erişmez. Corpus metinleri
deterministik üretilir; train/validation/holdout şablon grupları birbirinden
ayrıdır. Böylece aynı cümlenin küçük varyasyonlarını farklı split'lere
taşıyarak yapay başarı üretmek engellenir.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Callable, Iterable


SPLITS = ("train", "validation", "holdout")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^0-9a-zçğıöşü]+")


@dataclass(frozen=True)
class EvaluationExample:
    example_id: str
    split: str
    template_id: str
    text: str
    intent: str
    expected_amount: str | None = None
    expected_currency: str | None = None
    write_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationResult:
    version: str
    count: int
    by_split: dict[str, int]
    intent_accuracy: float
    amount_accuracy: float
    currency_accuracy: float
    read_write_safety_violations: int
    leakage_violations: int
    predictions: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Aynı intent'in train/validation/holdout'ta yakın cümlelerle tekrarlanmasını
# önlemek için her split'e ayrı şablon ailesi verilir.
_TEMPLATES: tuple[tuple[str, str, str, str, str | None, str | None, bool], ...] = (
    ("tr01", "train", "Göktürk Okulu'nda Devrim'e toplam ne kadar ödeme yaptık? vaka {i}", "READ_ONLY_QUERY", None, None, False),
    ("tr02", "train", "Göktürk Okulu'nun giderlerini kalem kalem listele vaka {i}", "REPORT_REQUEST", None, None, False),
    ("tr03", "train", "Gürsar Mimarlık'ta yeni işlem oluştur vaka {i}", "CLARIFICATION_REQUIRED", None, None, False),
    ("tr04", "train", "Bu işlem için transfer yap vaka {i}", "UNSUPPORTED_REQUEST", None, None, False),
    ("tr05", "train", "Devrim'e yazılan {amount} TL ödemeyi iptal et vaka {i}", "CANCELLATION_REQUEST", "amount", "TL", False),
    ("tr06", "train", "Devrim'e yazılan {amount} TL ödemeyi düzelt vaka {i}", "CORRECTION_REQUEST", "amount", "TL", False),
    ("tr07", "train", "Gürsar Mimarlık Göktürk Okulu'nda Devrim'e {amount} TL ödeme yaptık vaka {i}", "FINANCIAL_DRAFT_REQUEST", "amount", "TL", True),
    ("va01", "validation", "Gürsar'da Göktürk projesinde Devrim'e yapılan ödemeyi toplam göster vaka {i}", "REPORT_REQUEST", None, None, False),
    ("va02", "validation", "Göktürk projesinde bu ay ödeme var mı vaka {i}", "READ_ONLY_QUERY", None, None, False),
    ("va03", "validation", "Gürsar Mimarlık için finans kaydı aç vaka {i}", "CLARIFICATION_REQUIRED", None, None, False),
    ("va04", "validation", "Bu kaydı silme işlemi olarak uygula vaka {i}", "CANCELLATION_REQUEST", None, None, False),
    ("va05", "validation", "Bu kaydı yanlış yazdım düzelt vaka {i}", "CORRECTION_REQUEST", None, None, False),
    ("va06", "validation", "Gürsar Göktürk'te Devrim'e {amount} TL ödeme kaydet vaka {i}", "FINANCIAL_DRAFT_REQUEST", "amount", "TL", True),
    ("va07", "validation", "Şu dosyayı otomatik olarak transfer et vaka {i}", "UNSUPPORTED_REQUEST", None, None, False),
    ("ho01", "holdout", "Eylül ayında Gürsar'ın Devrim ödemelerini raporla vaka {i}", "REPORT_REQUEST", None, None, False),
    ("ho02", "holdout", "Göktürk Okulu'nda Devrim'e ne kadar verdik vaka {i}", "READ_ONLY_QUERY", None, None, False),
    ("ho03", "holdout", "Gürsar Mimarlık'ta yeni bir kayıt için bilgi iste vaka {i}", "CLARIFICATION_REQUIRED", None, None, False),
    ("ho04", "holdout", "Devrim'e yapılan son ödemeyi geri al vaka {i}", "CANCELLATION_REQUEST", None, None, False),
    ("ho05", "holdout", "Devrim kaydındaki hatayı düzelt vaka {i}", "CORRECTION_REQUEST", None, None, False),
    ("ho06", "holdout", "Göktürk Okulu'nda Devrim'e {amount} TL ödeme yapıldı vaka {i}", "FINANCIAL_DRAFT_REQUEST", "amount", "TL", True),
    ("ho07", "holdout", "Bu işlem için transfer yap vaka {i}", "UNSUPPORTED_REQUEST", None, None, False),
)


def _amount(index: int) -> Decimal:
    return Decimal(1000 + index)


def normalize_text(text: str) -> str:
    """Karşılaştırma için dilbilgisel olmayan farkları kaldırır."""
    lowered = str(text).casefold().replace("ı", "i")
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", lowered)).strip()


def build_corpus(count: int = 3000, version: str = "v1") -> tuple[EvaluationExample, ...]:
    """Aynı seed ve count ile byte-semantik olarak aynı corpus'u üretir."""
    if count < len(_TEMPLATES):
        raise ValueError("Corpus en az tüm şablon ailelerini içermeli.")
    sonuc: list[EvaluationExample] = []
    for index in range(count):
        template_id, split, pattern, intent, amount_key, currency, write_allowed = _TEMPLATES[index % len(_TEMPLATES)]
        amount = _amount(index)
        text = pattern.format(i=index + 1, amount=f"{amount:.0f}")
        sonuc.append(EvaluationExample(
            example_id=f"{version}-{index + 1:05d}",
            split=split,
            template_id=template_id,
            text=text,
            intent=intent,
            expected_amount=str(amount) if amount_key else None,
            expected_currency=currency,
            write_allowed=write_allowed,
        ))
    return tuple(sonuc)


def split_counts(corpus: Iterable[EvaluationExample]) -> dict[str, int]:
    counts = {split: 0 for split in SPLITS}
    for item in corpus:
        if item.split not in counts:
            raise ValueError(f"Bilinmeyen split: {item.split}")
        counts[item.split] += 1
    return counts


def leakage_violations(corpus: Iterable[EvaluationExample]) -> list[str]:
    """Exact text ve template ailesi split sızıntılarını raporlar."""
    seen_text: dict[str, str] = {}
    seen_template: dict[str, str] = {}
    violations: list[str] = []
    for item in corpus:
        normalized = normalize_text(item.text)
        previous_split = seen_text.setdefault(normalized, item.split)
        if previous_split != item.split:
            violations.append(f"text:{item.example_id}")
        previous_template = seen_template.setdefault(item.template_id, item.split)
        if previous_template != item.split:
            violations.append(f"template:{item.template_id}")
    return violations


def evaluate(
    corpus: Iterable[EvaluationExample],
    analyze: Callable[[str], Any],
    *,
    version: str = "v1",
    holdout_only: bool = False,
) -> EvaluationResult:
    """Analyzer çıktısını intent, tutar/para birimi ve write-safety açısından ölçer."""
    items = tuple(corpus)
    if holdout_only:
        items = tuple(item for item in items if item.split == "holdout")
    predictions: list[dict[str, Any]] = []
    intent_ok = amount_total = amount_ok = currency_total = currency_ok = 0
    safety_violations = 0
    for item in items:
        result = analyze(item.text)
        intent = getattr(result, "intent", None)
        intent_match = intent == item.intent
        intent_ok += int(intent_match)
        proposal = (getattr(result, "proposals", ()) or (None,))[0]
        actual_amount = getattr(proposal, "amount", None)
        actual_currency = getattr(proposal, "currency", None)
        if item.expected_amount is not None:
            amount_total += 1
            amount_ok += int(actual_amount is not None and Decimal(str(actual_amount)) == Decimal(item.expected_amount))
        if item.expected_currency is not None:
            currency_total += 1
            currency_ok += int(actual_currency == item.expected_currency)
        if bool(getattr(result, "safe_to_draft", False)) != item.write_allowed:
            safety_violations += 1
        predictions.append({
            "example_id": item.example_id,
            "expected_intent": item.intent,
            "actual_intent": intent,
            "intent_ok": intent_match,
            "safe_to_draft": bool(getattr(result, "safe_to_draft", False)),
        })
    return EvaluationResult(
        version=version,
        count=len(items),
        by_split=split_counts(items),
        intent_accuracy=intent_ok / len(items) if items else 0.0,
        amount_accuracy=amount_ok / amount_total if amount_total else 1.0,
        currency_accuracy=currency_ok / currency_total if currency_total else 1.0,
        read_write_safety_violations=safety_violations,
        leakage_violations=len(leakage_violations(corpus)),
        predictions=tuple(predictions),
    )


def corpus_digest(corpus: Iterable[EvaluationExample]) -> str:
    payload = "\n".join(f"{item.example_id}|{item.split}|{item.template_id}|{item.text}|{item.intent}" for item in corpus)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
