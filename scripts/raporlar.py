# -*- coding: utf-8 -*-
"""Rapor servisi.

Holding Overview, Cash Flow, 13-Week Cash Forecast, Receivables/Payables Aging,
Budget vs Actual, Project/Company/Business-Unit Profitability, Intercompany
Balances, Scenario Comparison. Muhasebe modeli yeterliyse (ortak hesap plani
mevcut) Trial Balance / P&L / Balance Sheet uretilir; aksi halde 'requires
accounting mapping' olarak isaretlenir.
"""

from __future__ import annotations

from butce_forecast import butce_gerceklesen
from finans_motoru import boyut_kirilimi, holding_kpi
from kurumsal_finans import konsolide_mizan
from nakit_13hafta import nakit_13_hafta
from senaryo_motoru import senaryo_karsilastir
from ticari import alacaklar, borclar


def holding_overview(faaliyet=None) -> dict:
    return {"tip": "holding_overview", "kpi": holding_kpi(faaliyet),
            "sirketler": boyut_kirilimi("sirket", faaliyet)}


def cash_flow(faaliyet=None) -> dict:
    from finans_cekirdegi import nakit_tahmini

    return {"tip": "cash_flow", "veri": nakit_tahmini(13, faaliyet)}


def cash_forecast_13(faaliyet=None, bugun=None) -> dict:
    return {"tip": "cash_forecast_13", **nakit_13_hafta(faaliyet=faaliyet, bugun=bugun)}


def receivables_aging(faaliyet=None, tarih=None) -> dict:
    return {"tip": "receivables_aging", **alacaklar(faaliyet, tarih)}


def payables_aging(faaliyet=None, tarih=None) -> dict:
    return {"tip": "payables_aging", **borclar(faaliyet, tarih)}


def budget_vs_actual(faaliyet="tum", baslangic=None, bitis=None, proje=None) -> dict:
    return {"tip": "budget_vs_actual", **butce_gerceklesen(faaliyet, baslangic, bitis, proje)}


def project_profitability(faaliyet=None) -> dict:
    return {"tip": "project_profitability", "kalemler": boyut_kirilimi("proje", faaliyet)}


def company_profitability() -> dict:
    return {"tip": "company_profitability", "kalemler": boyut_kirilimi("sirket")}


def business_unit_profitability() -> dict:
    return {"tip": "business_unit_profitability", "kalemler": boyut_kirilimi("faaliyet_alani")}


def intercompany_balances() -> dict:
    from datetime import date

    from kurumsal_finans import sirketler_arasi_mutabakat

    return {"tip": "intercompany_balances",
            "kalemler": sirketler_arasi_mutabakat(date(2000, 1, 1), date(2099, 12, 31))}


def scenario_comparison() -> dict:
    return {"tip": "scenario_comparison", **senaryo_karsilastir()}


def _mapping_yeterli() -> bool:
    """Ortak hesap plani ve eslemeler rapor uretmeye yeterli mi?"""
    from veri_katmani import baglanti, veritabani_hazirla

    veritabani_hazirla()
    with baglanti() as con:
        hesap = con.execute(
            "SELECT COUNT(*) n FROM hesaplar WHERE hesap_tipi IN "
            "('VARLIK','YUKUMLULUK','OZKAYNAK','GELIR','GIDER')"
        ).fetchone()["n"]
        esleme = con.execute("SELECT COUNT(*) n FROM hesap_eslemeleri").fetchone()["n"]
    return hesap >= 5 and esleme > 0


def trial_balance(baslangic="2000-01-01", bitis="2099-12-31") -> dict:
    if not _mapping_yeterli():
        return {"tip": "trial_balance", "durum": "Accounting mapping incomplete", "kalemler": []}
    kalemler = konsolide_mizan(baslangic, bitis, True)
    borc = round(sum(x["borc"] for x in kalemler), 2)
    alacak = round(sum(x["alacak"] for x in kalemler), 2)
    denk = abs(borc - alacak) < 0.01
    return {"tip": "trial_balance", "durum": "OK" if denk else "Accounting mapping incomplete",
            "borc_toplam": borc, "alacak_toplam": alacak, "denk": denk, "kalemler": kalemler}


def kar_zarar(baslangic="2000-01-01", bitis="2099-12-31") -> dict:
    """Yonetim P&L'i: gelir - gider (ortak hesap plani uzerinden)."""
    if not _mapping_yeterli():
        return {"tip": "kar_zarar", "durum": "Accounting mapping incomplete"}
    mizan = konsolide_mizan(baslangic, bitis, True)
    gelir = sum(x["alacak"] - x["borc"] for x in mizan if x["hesap_tipi"] == "GELIR")
    gider = sum(x["borc"] - x["alacak"] for x in mizan if x["hesap_tipi"] == "GIDER")
    return {"tip": "kar_zarar", "durum": "OK", "gelir": round(gelir, 2), "gider": round(gider, 2),
            "net": round(gelir - gider, 2), "kaynak": "ortak hesap plani (yonetim gorunumu)"}


def bilanco(baslangic="2000-01-01", bitis="2099-12-31") -> dict:
    """Yonetim bilanco ozeti; VARLIK = YUKUMLULUK + OZKAYNAK + DONEM KARI invariantini test eder."""
    if not _mapping_yeterli():
        return {"tip": "bilanco", "durum": "Accounting mapping incomplete"}
    mizan = konsolide_mizan(baslangic, bitis, True)

    def _top(tip: str) -> float:
        borc_normal = tip in {"VARLIK", "GIDER"}
        return round(sum((x["borc"] - x["alacak"]) if borc_normal else (x["alacak"] - x["borc"])
                         for x in mizan if x["hesap_tipi"] == tip), 2)

    varlik, yukumluluk, ozkaynak = _top("VARLIK"), _top("YUKUMLULUK"), _top("OZKAYNAK")
    donem_kari = round(_top("GELIR") - _top("GIDER"), 2)
    fark = round(varlik - (yukumluluk + ozkaynak + donem_kari), 2)
    denk = abs(fark) < 1
    return {"tip": "bilanco", "durum": "OK" if denk else "Accounting mapping incomplete",
            "varlik": varlik, "yukumluluk": yukumluluk, "ozkaynak": ozkaynak,
            "donem_kari": donem_kari, "fark": fark, "denk": denk,
            "kaynak": "ortak hesap plani (yonetim gorunumu)"}


RAPORLAR = {
    "holding-overview": holding_overview,
    "cash-flow": cash_flow,
    "cash-forecast-13": cash_forecast_13,
    "receivables-aging": receivables_aging,
    "payables-aging": payables_aging,
    "budget-vs-actual": budget_vs_actual,
    "project-profitability": project_profitability,
    "company-profitability": company_profitability,
    "business-unit-profitability": business_unit_profitability,
    "intercompany-balances": intercompany_balances,
    "scenario-comparison": scenario_comparison,
    "trial-balance": trial_balance,
    "kar-zarar": kar_zarar,
    "bilanco": bilanco,
}


def rapor_listesi() -> list[dict]:
    return [{"ad": ad, "baslik": ad.replace("-", " ").title()} for ad in RAPORLAR]


def rapor_getir(ad: str, **params) -> dict:
    import inspect

    fonksiyon = RAPORLAR.get(str(ad or "").strip().lower())
    if not fonksiyon:
        raise ValueError(f"Bilinmeyen rapor: {ad}")
    kabul = set(inspect.signature(fonksiyon).parameters)
    temiz = {k: v for k, v in params.items() if k in kabul}
    return fonksiyon(**temiz)

