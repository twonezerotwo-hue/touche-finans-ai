# -*- coding: utf-8 -*-
"""Ileri FP&A: duyarlilik analizi, basabas, tahmin dogrulugu (MAPE), Monte Carlo.

Monte Carlo istege bagli/ileri modul olarak calisir; production finansal
motorunun zorunlu bagimliligi degildir. Simulasyon ve pseudo-random uretim
backend tarafinda yapilir; arayuz yalnizca sonucu gosterir.
"""

from __future__ import annotations

import random
from datetime import datetime

from finans_motoru import bakiye_ozeti, holding_kpi, karlilik
from money import tl_metin, yuzde_metin
from veri_katmani import baglanti, veritabani_hazirla


def duyarlilik_analizi(faaliyet=None, maliyet_degisimleri=(-5, 5, 10, 20),
                       gelir_degisimleri=(-10, 0, 10)) -> dict:
    """Malzeme/maliyet ve gelir degisimlerinin brut marja etkisini tablolar."""
    ozet = bakiye_ozeti(faaliyet)
    ciro, maliyet = ozet["ciro_kurus"], ozet["maliyet_kurus"]
    satirlar = []
    for md in maliyet_degisimleri:
        yeni_maliyet = round(maliyet * (1 + md / 100.0))
        kar = karlilik(ciro, yeni_maliyet)
        satirlar.append({"maliyet_degisim_yuzde": md, "brut_kar_kurus": kar["brut_kar_kurus"],
                         "brut_marj_yuzde": kar["brut_marj_yuzde"],
                         "brut_marj_metin": yuzde_metin(kar["brut_marj_yuzde"])})
    gelir_satirlari = []
    for gd in gelir_degisimleri:
        yeni_ciro = round(ciro * (1 + gd / 100.0))
        kar = karlilik(yeni_ciro, maliyet)
        gelir_satirlari.append({"gelir_degisim_yuzde": gd, "brut_kar_kurus": kar["brut_kar_kurus"],
                                "brut_marj_yuzde": kar["brut_marj_yuzde"],
                                "brut_marj_metin": yuzde_metin(kar["brut_marj_yuzde"])})
    return {"faaliyet": faaliyet or "tum", "maliyet_duyarliligi": satirlar,
            "gelir_duyarliligi": gelir_satirlari,
            "mevcut_marj_metin": yuzde_metin(karlilik(ciro, maliyet)["brut_marj_yuzde"])}


def basabas(faaliyet=None, hedef_marj: float = 0.0) -> dict:
    """Mevcut maliyeti karsilayan (ve hedef marji saglayan) basabas ciro."""
    ozet = bakiye_ozeti(faaliyet)
    ciro, maliyet = ozet["ciro_kurus"], ozet["maliyet_kurus"]
    hedef = int(round(maliyet / (1 - hedef_marj / 100.0))) if hedef_marj < 100 else maliyet
    return {"faaliyet": faaliyet or "tum", "mevcut_ciro_kurus": ciro, "maliyet_kurus": maliyet,
            "basabas_ciro_kurus": hedef, "fark_kurus": ciro - hedef,
            "mevcut_ciro_metin": tl_metin(ciro / 100), "basabas_ciro_metin": tl_metin(hedef / 100),
            "hedef_marj": hedef_marj}


def mape(tahminler, gercekler) -> float:
    """Ortalama Mutlak Yuzde Hatasi (MAPE). Sifir gerceklesen atlanir."""
    if len(tahminler) != len(gercekler) or not tahminler:
        return 0.0
    toplam, adet = 0.0, 0
    for t, g in zip(tahminler, gercekler):
        if g == 0:
            continue
        toplam += abs((g - t) / g)
        adet += 1
    return round(toplam / adet * 100, 2) if adet else 0.0


def dogruluk_kaydet(donem: str, onceki_tahmin, gerceklesen, olcu: str = "NAKIT") -> dict:
    from money import kurus

    t, g = kurus(onceki_tahmin), kurus(gerceklesen)
    hata = g - t
    hata_yuzde = round(abs(hata) / abs(g) * 100, 2) if g else 0.0
    veritabani_hazirla()
    with baglanti() as con:
        con.execute(
            "INSERT INTO tahmin_dogrulugu(donem,olcu,onceki_tahmin_kurus,gerceklesen_kurus,"
            "hata_kurus,hata_yuzde,guncelleme) VALUES(?,?,?,?,?,?,?)",
            (str(donem), str(olcu), t, g, hata, hata_yuzde,
             datetime.now().isoformat(timespec="seconds")),
        )
    return {"donem": donem, "hata_kurus": hata, "hata_yuzde": hata_yuzde}


def dogruluk_ozeti() -> dict:
    veritabani_hazirla()
    with baglanti() as con:
        rows = con.execute("SELECT * FROM tahmin_dogrulugu ORDER BY id DESC LIMIT 200").fetchall()
    if not rows:
        return {"adet": 0, "mape": 0.0, "kayitlar": []}
    tahmin = [r["onceki_tahmin_kurus"] for r in rows]
    gercek = [r["gerceklesen_kurus"] for r in rows]
    return {"adet": len(rows), "mape": mape(tahmin, gercek),
            "kayitlar": [dict(r) for r in rows[:50]]}


def monte_carlo(faaliyet=None, iterasyon: int = 1000, tohum=None, esik=None,
                gelir_sapma: float = 8.0, maliyet_sapma: float = 8.0,
                gecikme_gun: float = 10.0) -> dict:
    """Tahsilat gecikmesi, gelir ve maliyet belirsizligiyle nakit riski olasiligi.

    Backend'de pseudo-random simulasyon calisir. Cikti: 13 haftada rezerv
    (esik) altina dusme ihtimali ve dagilim ozetleri.
    """
    from nakit_13hafta import nakit_13_hafta

    iterasyon = max(100, min(int(iterasyon), 100000))
    rnd = random.Random(tohum)
    kpi = holding_kpi(faaliyet)
    taban = nakit_13_hafta(faaliyet=faaliyet)
    esik = int(esik if esik is not None else kpi["korunacak_nakit_kurus"])
    ciro = kpi["toplam_ciro_kurus"]
    maliyet = kpi["toplam_maliyet_kurus"]
    tahsilat = kpi["beklenen_tahsilat_30_kurus"]
    minler, altinda = [], 0
    for _ in range(iterasyon):
        gelir_etki = ciro * rnd.gauss(0, gelir_sapma) / 100.0
        maliyet_etki = maliyet * rnd.gauss(0, maliyet_sapma) / 100.0
        gecikme_etki = tahsilat * max(0.0, rnd.gauss(gecikme_gun / 30.0, 0.1))
        sim_min = taban["min_kapanis_kurus"] + gelir_etki - maliyet_etki - gecikme_etki
        minler.append(sim_min)
        if sim_min < esik:
            altinda += 1
    minler.sort()
    def _yuzde(p):
        return minler[min(len(minler) - 1, int(len(minler) * p))]
    return {"faaliyet": faaliyet or "tum", "iterasyon": iterasyon, "esik_kurus": esik,
            "esik_metin": tl_metin(esik / 100),
            "olasilik_yuzde": round(altinda / iterasyon * 100, 1),
            "ortalama_min_kurus": int(sum(minler) / len(minler)),
            "p5_kurus": _yuzde(0.05), "p95_kurus": _yuzde(0.95),
            "ortalama_min_metin": tl_metin(sum(minler) / len(minler) / 100),
            "uyari": "Monte Carlo ileri/isteğe bağlı modüldür; finansal motoru bağımlılığı değildir."}

