# -*- coding: utf-8 -*-
"""Doviz kuru saglayici soyutlamasi ve TL cevrimi.

Harici kur API'si icin credential/ag yoksa `ManuelFxRateProvider` (veritabani)
kullanilir. Gercek saglayici icin `FxRateProvider` alt sinifi eklenir; boylece
uluslararasi kur entegrasyonu ayrintilarindan bagimsiz kalinir.
"""

from __future__ import annotations

from decimal import Decimal

from veri_katmani import baglanti, veritabani_hazirla


class FxRateProvider:
    """Kur saglayici arayuzu."""

    ad = "soyut"

    def kur(self, base: str, quote: str, tarih=None) -> Decimal:
        """1 base = ? quote kurunu dondurur."""
        raise NotImplementedError


class ManuelFxRateProvider(FxRateProvider):
    """Veritabanindaki (`kurlar`) kayitli kurlari kullanir. TL tabandir."""

    ad = "manuel"

    def kur(self, base: str, quote: str, tarih=None) -> Decimal:
        base, quote = str(base).upper(), str(quote).upper()
        if base == quote:
            return Decimal("1")
        if quote == "TL":
            return self._tl(base, tarih)
        if base == "TL":
            return Decimal("1") / self._tl(quote, tarih)
        # base -> TL -> quote
        return self._tl(base, tarih) / self._tl(quote, tarih)

    def _tl(self, pb: str, tarih) -> Decimal:
        veritabani_hazirla()
        with baglanti() as con:
            if tarih:
                row = con.execute(
                    "SELECT kur_mikro FROM kurlar WHERE para_birimi=? AND tarih<=? "
                    "ORDER BY tarih DESC LIMIT 1", (pb, str(tarih)),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT kur_mikro FROM kurlar WHERE para_birimi=? ORDER BY tarih DESC LIMIT 1", (pb,),
                ).fetchone()
        if not row:
            raise ValueError(f"Kur bulunamadi: {pb}")
        return Decimal(row["kur_mikro"]) / Decimal(1_000_000)


class TcmbFxRateProvider(FxRateProvider):
    """TCMB kur saglayici iskeleti.

    Ag/credential gerektirir; bu ortamda varsayilan olarak kullanilmaz.
    `requests` ile TCMB gunluk kur XML'i cekilecek sekilde genisletilebilir.
    """

    ad = "tcmb"

    def kur(self, base: str, quote: str, tarih=None) -> Decimal:  # pragma: no cover - ag gerekir
        raise NotImplementedError("TCMB saglayicisi icin ag erisimi yapilandirilmali.")


_SAGLAYICILAR = {"manuel": ManuelFxRateProvider, "tcmb": TcmbFxRateProvider}


def saglayici_getir(ad: str = "manuel") -> FxRateProvider:
    return _SAGLAYICILAR.get(str(ad or "manuel").lower(), ManuelFxRateProvider)()


def kur_cevir(tutar, kaynak_pb: str, hedef_pb: str = "TL", tarih=None, saglayici: str = "manuel") -> dict:
    """Tutari kaynak para biriminden hedef para birimine cevirir."""
    from money import _decimal, kurus

    k = saglayici_getir(saglayici).kur(kaynak_pb, hedef_pb, tarih)
    sonuc = _decimal(tutar) * k
    return {"tutar": float(sonuc.quantize(Decimal("0.01"))), "kur": float(k),
            "kaynak_pb": str(kaynak_pb).upper(), "hedef_pb": str(hedef_pb).upper(),
            "saglayici": saglayici, "kurus": kurus(sonuc), "tarih": str(tarih) if tarih else None}
