# -*- coding: utf-8 -*-
"""13 haftalik nakit akis projeksiyonu.

Acik (vadesi girilmis) alacak ve borc fislerinden haftalik plan uretir.
Kolonlar: Opening Cash, Expected Collections, Other Inflows, Supplier Payments,
Payroll, Tax, Operating Expenses, Investments, Financing, Closing Cash.
Kapanis nakit minimum esik altina duserse risk uretir.
"""

from __future__ import annotations

from datetime import date, timedelta

from money import tl_metin
from veri_katmani import baglanti, veritabani_hazirla

HESAP_BUCKET = {
    "120": "collections", "131": "collections", "159": "collections",
    "320": "supplier", "335": "payroll", "391": "tax",
    "331": "opex", "336": "opex", "300": "financing",
}
_HESAPLAR = tuple(HESAP_BUCKET.keys())


def _hafta_basi(gun: date) -> date:
    return gun - timedelta(days=gun.weekday())


def _kirilim(faaliyet, bas, son):
    veritabani_hazirla()
    where, args = ["f.tarih BETWEEN ? AND ?"], [bas.isoformat(), son.isoformat()]
    if faaliyet and faaliyet not in {"tum", "touche_holding"}:
        where.append("f.faaliyet_key=?")
        args.append(faaliyet)
    with baglanti() as con:
        rows = con.execute(
            "SELECT f.vade, s.hesap_kodu, s.borc_kurus, s.alacak_kurus FROM fisler f "
            "JOIN fis_satirlari s ON s.fis_id=f.fis_id WHERE s.hesap_kodu IN (" +
            ",".join("?" for _ in _HESAPLAR) + ") AND TRIM(COALESCE(f.vade,''))<>'' AND " +
            " AND ".join(where),
            [*_HESAPLAR, *args],
        ).fetchall()
    return rows


def nakit_13_hafta(faaliyet=None, bugun=None, baslangic_nakit=None, rezerv_gun: int = 30,
                   yatirim_takvimi=None, hafta_sayisi: int = 13) -> dict:
    """13 haftalik nakit projeksiyonu ve esik riski uretir."""
    from finans_motoru import bakiye_ozeti, korunacak_nakit

    bugun = bugun or date.today()
    ilk = _hafta_basi(bugun)
    haftalar = [ilk + timedelta(days=7 * i) for i in range(hafta_sayisi)]
    son = ilk + timedelta(days=7 * hafta_sayisi)
    if baslangic_nakit is None:
        baslangic_nakit = bakiye_ozeti(faaliyet)["nakit_kurus"]
    baslangic_nakit = int(baslangic_nakit)
    esik = korunacak_nakit(faaliyet, bugun, rezerv_gun)

    kova = {h: {"collections": 0, "other_inflows": 0, "supplier": 0, "payroll": 0,
                "tax": 0, "opex": 0, "financing": 0} for h in haftalar}
    for r in _kirilim(faaliyet, ilk, son):
        try:
            vade = date.fromisoformat(str(r["vade"])[:10])
        except ValueError:
            continue
        hb = _hafta_basi(vade)
        if hb not in kova:
            continue
        bucket = HESAP_BUCKET.get(r["hesap_kodu"])
        if not bucket:
            continue
        if bucket == "collections":
            kova[hb][bucket] += max(0, r["borc_kurus"] - r["alacak_kurus"])
        else:
            kova[hb][bucket] += max(0, r["alacak_kurus"] - r["borc_kurus"])

    yatirim = yatirim_takvimi or {}
    kapanis = baslangic_nakit
    liste = []
    for i, h in enumerate(haftalar, 1):
        v = kova[h]
        yat = int(yatirim.get(i, yatirim.get(str(i), 0)) or 0)
        giris = v["collections"] + v["other_inflows"]
        cikis = v["supplier"] + v["payroll"] + v["tax"] + v["opex"] + v["financing"] + yat
        kapanis = kapanis + giris - cikis
        liste.append({
            "hafta_no": i, "hafta_basi": h.isoformat(),
            "acilis_kurus": kapanis - giris + cikis,
            "tahsilat_kurus": v["collections"], "diger_giris_kurus": v["other_inflows"],
            "tedarikci_kurus": v["supplier"], "personel_kurus": v["payroll"],
            "vergi_kurus": v["tax"], "isletme_gider_kurus": v["opex"],
            "yatirim_kurus": yat, "finansman_kurus": v["financing"],
            "kapanis_kurus": kapanis,
            "esik_alti": kapanis < esik,
        })
        liste[-1]["kapanis_metin"] = tl_metin(kapanis / 100)
    min_h = min(liste, key=lambda x: x["kapanis_kurus"])
    return {
        "faaliyet": faaliyet or "tum", "bugun": bugun.isoformat(),
        "baslangic_nakit_kurus": baslangic_nakit, "esik_kurus": esik,
        "esik_metin": tl_metin(esik / 100),
        "haftalar": liste,
        "min_kapanis_kurus": min_h["kapanis_kurus"], "min_hafta": min_h["hafta_no"],
        "risk": min_h["kapanis_kurus"] < esik,
    }
