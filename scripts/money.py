# -*- coding: utf-8 -*-
"""Para (Money) değer nesnesi ve Türkçe yerel biçimlendirme.

Finans hesaplarında kayan noktalı (float) aritmetik kullanılmaz. Tutarlar
kuruş hassasiyetinde tamsayı (minor unit) olarak taşınır ve gerektiğinde
Decimal'e çevrilir. Böylece 0.1 + 0.2 gibi yuvarlama hataları finans
sonuçlarına sızmaz.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Para birimi -> (sembol, ondalık basamak)
PARA_BIRIMLERI = {
    "TL": ("\u20ba", 2),
    "TRY": ("\u20ba", 2),
    "EUR": ("\u20ac", 2),
    "USD": ("$", 2),
    "GBP": ("\u00a3", 2),
}
VARSAYILAN_PB = "TL"


def _decimal(deger) -> Decimal:
    """Metin/ondalık/tamsayıyı güvenli biçimde Decimal'e çevirir."""
    if isinstance(deger, Decimal):
        return deger
    if deger in (None, ""):
        return Decimal("0")
    if isinstance(deger, int):
        return Decimal(deger)
    if isinstance(deger, float):
        # float yalnızca kaynak veri başka türlü verilemiyorsa kabul edilir;
        # str üzerinden çevirerek ikili kayan nokta gürültüsünü temizleriz.
        return Decimal(repr(deger))
    metin = str(deger).strip().replace("\u20ba", "").replace(" ", "")
    if "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        sonuc = Decimal(metin)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Gecersiz tutar: {deger}") from exc
    if not sonuc.is_finite():
        raise ValueError(f"Sonlu olmayan tutar: {deger}")
    return sonuc


def kurus(deger) -> int:
    """Tutarı kuruş (minor unit) tamsayısına çevirir; yarım yukarı yuvarlar."""
    return int((_decimal(deger) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tl_metin(deger, *, kusuratsiz: bool = True, pb: str = "TL") -> str:
    """Tutari Turkce yerel bicimde dondurur: 1234567.5 -> '1.250.000,00'."""
    d = _decimal(deger)
    sembol = PARA_BIRIMLERI.get(str(pb).upper(), ("", 2))[0]
    isaret = "-" if d < 0 else ""
    d = abs(d)
    if kusuratsiz:
        govde = f"{d.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,.0f}"
    else:
        govde = f"{d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    govde = govde.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{isaret}{sembol}{govde}"


def yuzde_metin(deger, basamak: int = 1) -> str:
    """Orani Turkce bicimde dondurur: 24.5 -> '%24,5'. Deger yoksa (None) tire."""
    if deger is None:
        return "\u2014"
    d = _decimal(deger)
    isaret = "-" if d < 0 else ""
    d = abs(d)
    govde = f"{d.quantize(Decimal('1.' + '0' * basamak), rounding=ROUND_HALF_UP):.{basamak}f}"
    return f"%{isaret}{govde.replace('.', ',')}"


@dataclass(frozen=True)
class Money:
    """Para birimi tasiyan, kayan noktasiz tutar nesnesi."""

    kurus: int
    para_birimi: str = VARSAYILAN_PB

    def __post_init__(self):
        object.__setattr__(self, "kurus", int(self.kurus))
        object.__setattr__(self, "para_birimi", str(self.para_birimi or VARSAYILAN_PB).upper())

    @classmethod
    def from_tutar(cls, tutar, para_birimi: str = VARSAYILAN_PB) -> "Money":
        return cls(kurus(tutar), para_birimi)

    def to_decimal(self) -> Decimal:
        return (Decimal(self.kurus) / 100).quantize(Decimal("0.01"))

    @property
    def tutar(self) -> float:
        """Yalnizca gorunum/aralik kontrolu icin; hesapta kullanilmaz."""
        return float(self.kurus) / 100

    def _ayni_pb(self, digeri: "Money") -> None:
        if self.para_birimi != digeri.para_birimi:
            raise ValueError(
                f"Para birimleri farkli: {self.para_birimi} ve {digeri.para_birimi}. "
                "Once donusturun (Money.donustur)."
            )

    def __add__(self, digeri: "Money") -> "Money":
        self._ayni_pb(digeri)
        return Money(self.kurus + digeri.kurus, self.para_birimi)

    def __sub__(self, digeri: "Money") -> "Money":
        self._ayni_pb(digeri)
        return Money(self.kurus - digeri.kurus, self.para_birimi)

    def __mul__(self, carpan) -> "Money":
        return Money(kurus(self.to_decimal() * _decimal(carpan)), self.para_birimi)

    def __neg__(self) -> "Money":
        return Money(-self.kurus, self.para_birimi)

    def __abs__(self) -> "Money":
        return Money(abs(self.kurus), self.para_birimi)

    def donustur(self, kur, hedef_pb: str = "TL") -> "Money":
        """Verilen kur ile baska bir para birimine cevirir (1 birim kaynak = kur hedef)."""
        k = _decimal(kur)
        return Money(kurus(self.to_decimal() * k), hedef_pb)

    def bicim(self, *, kusuratsiz: bool = True) -> str:
        return tl_metin(self.to_decimal(), kusuratsiz=kusuratsiz, pb=self.para_birimi)

    def __str__(self) -> str:  # pragma: no cover - kolaylik
        return self.bicim()
