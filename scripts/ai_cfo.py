# -*- coding: utf-8 -*-
"""AI CFO katmani.

Mimari: DATABASE -> FINANCIAL ENGINE -> STRUCTURED CFO CONTEXT -> AI -> EXPLANATION.
AI sayilari KENDI toplamaz; butun sayilar merkezi finans motorundan gelir. AI
veri bulamazsa sayi uydurmaz, 'Bu veri sistemde mevcut degil.' der. Kritik
finansal islemleri otomatik gerceklestirmez; onay gerekir.
"""

from __future__ import annotations

import unicodedata
from datetime import date

from finans_motoru import boyut_kirilimi, holding_kpi, proje_karlilik
from nakit_13hafta import nakit_13_hafta
from ticari import alacaklar, borclar, yaklasan_odemeler
from veri_katmani import baglanti, veritabani_hazirla

VERI_YOK = "Bu veri sistemde mevcut değil."


def _norm(metin) -> str:
    s = unicodedata.normalize("NFKD", str(metin or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().replace("ı", "i")


def veri_tarihi() -> str:
    """Defterdeki en guncel islem tarihi (data through)."""
    veritabani_hazirla()
    with baglanti() as con:
        row = con.execute("SELECT MAX(tarih) t FROM fisler").fetchone()
    return str(row["t"]) if row and row["t"] else date.today().isoformat()


def cfo_baglam(faaliyet=None) -> dict:
    """Yapilandirilmis CFO baglami (AI'ya yalniz bu verilir)."""
    kpi = holding_kpi(faaliyet)
    alacak = alacaklar(faaliyet)
    borc = borclar(faaliyet)
    n13 = nakit_13_hafta(faaliyet)
    projeler = proje_karlilik(faaliyet)
    return {
        "veri_tarihi": veri_tarihi(),
        "faaliyet": faaliyet or "tum",
        "kpi": kpi,
        "alacak": {"toplam": alacak["toplam"], "gecikmis": alacak["gecikmis"],
                   "gecikmis_oran": alacak["gecikmis_oran"], "kalemler": alacak["kalemler"][:10]},
        "borc": {"toplam": borc["toplam"], "gecikmis": borc["gecikmis"]},
        "nakit_13hafta": {"min_kapanis_kurus": n13["min_kapanis_kurus"], "min_hafta": n13["min_hafta"],
                          "risk": n13["risk"], "esik_kurus": n13["esik_kurus"]},
        "projeler": projeler,
    }


def _cevap(metin, baglam, kanitlar=None, oneri=None) -> dict:
    return {"cevap": metin, "veri_tarihi": baglam["veri_tarihi"], "kanitlar": kanitlar or [],
            "oneri": oneri, "llm": "deterministik", "kaynak": "finans_motoru"}


def soru_cevapla(soru: str, faaliyet=None) -> dict:
    """Kullanici sorusunu deterministik finans motoru sonuclariyla yanitlar."""
    s = _norm(soru)
    baglam = cfo_baglam(faaliyet)
    kpi = baglam["kpi"]
    if any(k in s for k in ["yatirim", "yatırım", "ne kadar yatirim yapabilirim"]):
        return _cevap(
            f"Rezerv sonrası yatırım kapasitesi {kpi['metin']['yatirim_kapasitesi']}. "
            f"(Kullanılabilir nakit {kpi['metin']['kullanilabilir_nakit']}, korunacak {kpi['metin']['korunacak_nakit']}.)",
            baglam, kanitlar=[f"Toplam nakit {kpi['metin']['toplam_nakit']}"],
            oneri="Yatırım öncesi 13 haftalık minimum nakit noktasını doğrulayın.")
    if any(k in s for k in ["nakit risk", "nakit riski", "risk var", "30 gun", "30 gün", "likidite"]):
        n = baglam["nakit_13hafta"]
        durum = "riskli" if n["risk"] else "güvenli"
        return _cevap(
            f"13 haftalık projeksiyonda en düşük kapanış {n['min_hafta']}. haftada "
            f"({_tl(n['min_kapanis_kurus'])}) gerçekleşiyor; eşik {_tl(n['esik_kurus'])}. Durum: {durum}.",
            baglam, oneri="Riskli ise en büyük alacakların tahsilatını öne çekin." if n["risk"] else None)
    if any(k in s for k in ["marj", "en dusuk", "en düşük", "karlilik", "kârlılık"]):
        if not baglam["projeler"]:
            return _cevap(VERI_YOK, baglam)
        en_dusuk = min(baglam["projeler"], key=lambda x: x["brut_marj_yuzde"])
        return _cevap(
            f"En düşük brüt marj {en_dusuk['ad']} ({en_dusuk['brut_marj_metin']}).",
            baglam, kanitlar=[f"{p['ad']}: {p['brut_marj_metin']}" for p in baglam["projeler"][:5]])
    if any(k in s for k in ["alacak", "tahsilat", "problemli", "gecikmis"]):
        a = baglam["alacak"]
        if not a["kalemler"]:
            return _cevap(VERI_YOK, baglam)
        en_buyuk = max(a["kalemler"], key=lambda x: x["toplam"])
        return _cevap(
            f"Toplam alacak {_tl(a['toplam'] * 100)}; gecikmis {_tl(a['gecikmis'] * 100)} "
            f"(%{a['gecikmis_oran']}). En büyük açık: {en_buyuk['cari']} ({en_buyuk['toplam_metin']}).",
            baglam, kanitlar=[f"{k['cari']}: {k['toplam_metin']}" for k in a["kalemler"][:5]],
            oneri="Gecikmis kalemler için tahsilat aksiyonu planlayın.")
    if any(k in s for k in ["borc", "borç", "odeme", "ödeme", "tedarikci"]):
        b = baglam["borc"]
        yaklasan = yaklasan_odemeler(faaliyet, gun=30)
        return _cevap(
            f"Toplam borç {_tl(b['toplam'] * 100)}. 30 gün içinde {len(yaklasan)} ödeme var.",
            baglam, kanitlar=[f"{y['cari']}: {y['tutar_metin']}" for y in yaklasan[:5]])
    # faaliyet/şirket adı geçiyorsa kırılım ara
    bulunan = [x for x in boyut_kirilimi("sirket") if _norm(x["ad"]) in s or _norm(x["anahtar"]) in s]
    if bulunan:
        x = bulunan[0]
        return _cevap(f"{x['ad']}: ciro {x['ciro_metin']}, brüt marj {x['brut_marj_metin']}.",
                      baglam, kanitlar=[f"Nakit {x['nakit_kurus']/100:,.0f}"])
    # ozet + veri yok notu
    return _cevap(
        f"Holding özeti: nakit {kpi['metin']['toplam_nakit']}, alacak {kpi['metin']['toplam_alacak']}, "
        f"borç {kpi['metin']['toplam_borc']}. Belirli bir soru için alacak/marj/nakit/borç diye sorun. "
        f"{VERI_YOK if not kpi['toplam_nakit_kurus'] and not kpi['toplam_alacak_kurus'] else ''}".strip(),
        baglam)


def _tl(kurus) -> str:
    from money import tl_metin

    return tl_metin(float(kurus) / 100)


def insights_uret(faaliyet=None, hedef_marj: float = 15.0) -> list[dict]:
    """Otomatik CFO insight'lari (type/severity/title/description/entity/metric)."""
    from datetime import datetime

    baglam = cfo_baglam(faaliyet)
    tespit = datetime.now().isoformat(timespec="seconds")
    sonuc = []
    n = baglam["nakit_13hafta"]
    if n["risk"]:
        sonuc.append({"type": "CASH_RISK", "severity": "CRITICAL",
                      "title": "13 haftalık nakit riski",
                      "description": f"En düşük kapanış {_tl(n['min_kapanis_kurus'])} "
                                     f"(eşik {_tl(n['esik_kurus'])}); {n['min_hafta']}. haftada.",
                      "entity": faaliyet or "tum", "metric": "nakit", "detectedAt": tespit,
                      "status": "OPEN",
                      "oneri": "En büyük alacakların tahsilatını öne çekin, yatırımı erteleyin."})
    for p in baglam["projeler"]:
        if p["brut_marj_yuzde"] < 0:
            sonuc.append({"type": "NEGATIVE_MARGIN", "severity": "CRITICAL",
                          "title": f"{p['ad']} zararda", "description": f"Brüt marj {p['brut_marj_metin']}.",
                          "entity": p["ad"], "metric": "brut_marj", "detectedAt": tespit, "status": "OPEN",
                          "oneri": "Maliyet ve fiyat revizyonu değerlendirin."})
        elif p["brut_marj_yuzde"] < hedef_marj:
            sonuc.append({"type": "LOW_MARGIN", "severity": "WARNING",
                          "title": f"{p['ad']} marjı hedef altında",
                          "description": f"Brüt marj {p['brut_marj_metin']} (hedef %{hedef_marj}).",
                          "entity": p["ad"], "metric": "brut_marj", "detectedAt": tespit, "status": "OPEN"})
    a = baglam["alacak"]
    if a["gecikmis_oran"] >= 40 and a["kalemler"]:
        en = max(a["kalemler"], key=lambda x: sum(x[b] for b in ("1_30", "31_60", "61_90", "90_ustu")))
        sonuc.append({"type": "OVERDUE_RECEIVABLE", "severity": "WARNING",
                      "title": "Alacakların önemli kısmı gecikmiş",
                      "description": f"Gecikmiş oran %{a['gecikmis_oran']}. En riskli: {en['cari']}.",
                      "entity": en["cari"], "metric": "alacak_gecikme", "detectedAt": tespit, "status": "OPEN",
                      "oneri": "Gecikmiş kalemler için tahsilat planı oluşturun."})
    if not sonuc:
        sonuc.append({"type": "OK", "severity": "INFO", "title": "Kritik finansal sinyal yok",
                      "description": "Nakit, marj ve alacak göstergeleri izleme aralığında.",
                      "entity": faaliyet or "tum", "metric": "genel", "detectedAt": tespit, "status": "OPEN"})
    return sonuc


