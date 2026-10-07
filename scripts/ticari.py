# -*- coding: utf-8 -*-
"""Ticari alacak (AR) ve borc (AP) yaslandirma servisi.

Mevcut merkezi defteri kullanir; muster/tedarikci, sirket, proje, vade
bazinda acik kalemleri 0-30 / 31-60 / 61-90 / 90+ kovalarina ayirir ve
gecikmis kalemleri isaretler. Tutarlar kuruş tamsayisidir.
"""

from __future__ import annotations

from datetime import date, timedelta

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
    """Vadesi yaklasan borc kalemlerini (320) listeler."""
    from finans_cekirdegi import _tarih
    from veri_katmani import baglanti, veritabani_hazirla

    veritabani_hazirla()
    bugun = bugun or date.today()
    son = bugun + timedelta(days=max(1, int(gun)))
    where, args = ["s.hesap_kodu='320'", "TRIM(COALESCE(f.cari,''))<>''"], []
    if faaliyet and faaliyet not in {"tum", "touche_holding"}:
        where.append("f.faaliyet_key=?")
        args.append(faaliyet)
    with baglanti() as con:
        rows = con.execute(
            "SELECT f.faaliyet_key,f.proje,f.cari,f.vade,SUM(s.alacak_kurus-s.borc_kurus) bakiye "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id WHERE " + " AND ".join(where) +
            " GROUP BY f.faaliyet_key,f.proje,f.cari,f.vade HAVING bakiye>0",
            args,
        ).fetchall()
    sonuc = []
    for r in rows:
        vade = str(r["vade"] or "").strip()
        vade_gun = None
        if vade:
            try:
                vade_gun = (_tarih(vade) - bugun).days
            except ValueError:
                vade_gun = None
        if vade_gun is not None and vade_gun > int(gun):
            continue
        sonuc.append({
            "faaliyet": r["faaliyet_key"], "proje": r["proje"], "cari": r["cari"],
            "vade": vade or None, "kalan_gun": vade_gun, "tutar": round(r["bakiye"] / 100, 2),
            "tutar_metin": tl_metin(r["bakiye"] / 100),
            "gecikmis": vade_gun is not None and vade_gun < 0,
        })
    sonuc.sort(key=lambda x: (x["kalan_gun"] if x["kalan_gun"] is not None else 10**9))
    return sonuc[: max(1, int(limit))]
