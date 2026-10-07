# -*- coding: utf-8 -*-
"""Butce - Gerceklesen - Taahhut - Tahmin (EAC) servisi.

Her proje/sirket/faaliyet ve is kalemi icin Butce, Taahhut (committed),
Gerceklesen, Tahmin (EAC) ve Sapma alanlarini saglar. Hesaplar merkezi
finans motoru ve kurumsal butce raporundan gelir; UI elle toplamaz.
"""

from __future__ import annotations

from kurumsal_finans import butce_sapma_raporu
from finans_motoru import is_kalemi_grid
from money import tl_metin, yuzde_metin


def butce_gerceklesen(faaliyet="tum", baslangic=None, bitis=None, proje=None) -> dict:
    """Butce/gerceklesen/sapma raporunu risk bilgisiyle zenginlestirir."""
    from datetime import date

    bugun = date.today()
    bas = baslangic or bugun.replace(day=1)
    son = bitis or bugun
    rapor = butce_sapma_raporu(faaliyet, bas, son, proje)
    kalemler = []
    for k in rapor["kalemler"]:
        eac = _eac(k["butce"], k["gerceklesen"])
        oran = k["sapma_orani"]
        if k["ad"] and k["ad"].lower().find("sat") >= 0:  # pragma: no cover - sezgisel
            durum = "OLUMLU" if k["sapma"] >= 0 else "OLUMSUZ"
        else:
            durum = k["durum"]
        kalemler.append({**k, "eac": round(eac, 2), "eac_metin": tl_metin(eac),
                         "butce_metin": tl_metin(k["butce"]), "gerceklesen_metin": tl_metin(k["gerceklesen"]),
                         "sapma_metin": tl_metin(k["sapma"]), "sapma_orani_metin": yuzde_metin(oran),
                         "risk": "danger" if durum == "OLUMSUZ" and abs(oran) >= 20 else
                                 ("warning" if durum == "OLUMSUZ" else "healthy")})
    return {**rapor, "kalemler": kalemler}


def _eac(butce: float, gerceklesen: float) -> float:
    """Basit EAC: gerceklesen, butceyi astiysa gerceklesen; aksi halde butce."""
    return max(float(butce or 0), float(gerceklesen or 0))


def is_kalemleri_butce(faaliyet=None, proje=None) -> list[dict]:
    """Is kalemi bazinda Butce/Taahhut/Gerceklesen/Tahmin + kar/marj grid'i."""
    return is_kalemi_grid(faaliyet, proje)


def taahhut_ozeti(faaliyet=None, proje=None) -> dict:
    """Is kalemlerinden toplam butce/taahhut/gerceklesen/tahmin ozeti."""
    grid = is_kalemi_grid(faaliyet, proje)
    ozet = {"butce": sum(x["butce_kurus"] for x in grid), "taahhut": sum(x["taahhut_kurus"] for x in grid),
            "gerceklesen": sum(x["gerceklesen_kurus"] for x in grid), "tahmin": sum(x["tahmin_kurus"] for x in grid),
            "ciro": sum(x["ciro_kurus"] for x in grid)}
    ozet["metin"] = {k: tl_metin(v / 100) for k, v in ozet.items()}
    return ozet
