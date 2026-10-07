# -*- coding: utf-8 -*-
"""Ticari alacak (AR) ve borc (AP) yaslandirma servisi.

Mevcut merkezi defteri kullanir; muster/tedarikci, sirket, proje, vade
bazinda acik kalemleri 0-30 / 31-60 / 61-90 / 90+ kovalarina ayirir ve
gecikmis kalemleri isaretler. Tutarlar kuruş tamsayisidir.
"""

from __future__ import annotations

from datetime import date

from kurumsal_finans import cari_yaslandirma
from money import tl_metin

KOVALAR = ("1_30", "31_60", "61_90", "90_ustu")
KOVA_ETIKET = {"vadesi_gelmemis": "Vadesi gelmemis", "1_30": "0-30 gun",
               "31_60": "31-60 gun", "61_90": "61-90 gun", "90_ustu": "90+ gun"}


def yaslandirma(hesap: str = "120", faaliyet=None, rapor_tarihi=None) -> dict:
    """AR (120) veya AP (320) yaslandirmasini ozet + kalem listesi olarak dondurur."""
    hesap = str(hesap)
    if hesap not in {"120", "320"}:
        raise ValueError("Yaslandirma hesabi 120 (alacak) veya 320 (borc) olmali.")
    kalemler = cari_yaslandirma(hesap, faaliyet, rapor_tarihi)
    toplam = sum(k["toplam"] for k in kalemler)
    gecikmis = sum(sum(k[b] for b in KOVALAR) for k in kalemler)
    kova_toplam = {b: sum(k[b] for k in kalemler) for b in ("vadesi_gelmemis", *KOVALAR)}
    return {
        "hesap": hesap, "tip": "ALACAK" if hesap == "120" else "BORC",
        "toplam": round(toplam, 2), "gecikmis": round(gecikmis, 2),
        "gecikmis_oran": round(gecikmis / toplam * 100, 1) if toplam else 0.0,
        "kovalar": {KOVA_ETIKET[b]: round(kova_toplam[b], 2) for b in kova_toplam},
        "kalemler": [
            {**k, "gecikmis": sum(k[b] for b in KOVALAR) > 0,
             "gecikmis_metin": tl_metin(sum(k[b] for b in KOVALAR)),
             "toplam_metin": tl_metin(k["toplam"])}
            for k in kalemler
        ],
    }


def alacaklar(faaliyet=None, rapor_tarihi=None) -> dict:
    return yaslandirma("120", faaliyet, rapor_tarihi)


def borclar(faaliyet=None, rapor_tarihi=None) -> dict:
    return yaslandirma("320", faaliyet, rapor_tarihi)


def yaklasan_odemeler(faaliyet=None, bugun=None, gun: int = 30, limit: int = 50) -> list[dict]:
    """Netlenmiş ticari borçları ve sıradaki kredi taksidini tek ödeme planında listeler."""
    from finans_danismani import odeme_oncelikleri

    bugun = bugun or date.today()
    sonuc = []
    faaliyet_key = faaliyet or "tum"
    for odeme in odeme_oncelikleri(faaliyet_key, bugun=bugun, limit=50):
        kalan = odeme.get("kalan_gun")
        if kalan is None or kalan > int(gun):
            continue
        sonuc.append({
            "faaliyet": odeme["faaliyet"], "proje": odeme.get("proje") or "",
            "cari": odeme["cari"], "vade": odeme.get("vade"), "kalan_gun": kalan,
            "tutar": round(float(odeme["tutar"]), 2), "tutar_metin": tl_metin(odeme["tutar"]),
            "gecikmis": kalan < 0, "durum": odeme.get("durum"),
            "kaynak": odeme.get("kaynak", "Merkez finans defteri"),
        })
    sonuc.sort(key=lambda x: x["kalan_gun"])
    return sonuc[: max(1, int(limit))]
