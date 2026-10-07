# -*- coding: utf-8 -*-
"""Merkezi finans hesaplama motoru (single source of truth).

UI/AI hicbir finansal degeri elle hesaplamaz; nakit, alacak, borc, ciro,
maliyet, brut kar/marj, butce sapmasi, isletme sermayesi, yatirim kapasitesi
ve 13 haftalik minimum nakit bu modulden gelir. Butun tutarlar kuruş (minor
unit) tamsayisi olarak tasinir; donus degerleri TL tutar + ham kuruş icerir.

Hiyerarsi:
    holding -> faaliyet alani -> sirket(faaliyet) -> proje -> is kalemi
"""

from __future__ import annotations

from datetime import date, timedelta

from money import tl_metin, yuzde_metin
from veri_katmani import baglanti, veritabani_hazirla

NAKIT_HESAPLAR = ("100", "101", "102")
ALACAK_HESAPLAR = ("120", "131", "159")
BORC_HESAPLAR = ("300", "320", "331", "335", "336")
GELIR_HESAPLAR = ("600", "646", "649")
GIDER_HESAPLAR = ("740", "770", "780", "799")


def _in(hesaplar) -> str:
    return ",".join("?" for _ in hesaplar)


def _kimlik_filtre(faaliyet=None, proje=None) -> tuple[list[str], list]:
    where, args = [], []
    if faaliyet and faaliyet not in {"tum", "touche_holding"}:
        where.append("f.faaliyet_key=?")
        args.append(faaliyet)
    if proje:
        where.append("f.proje=?")
        args.append(proje)
    return where, args


def bakiye_ozeti(faaliyet=None, proje=None, yil=None, ay=None) -> dict:
    """Nakit, alacak, borc, ciro ve maliyet bakiyelerini kuruş olarak toplar.

    Konsolidasyon (tum/holding) seviyesinde grup ici (intercompany) 131/331
    bakiyeleri ELIMINE edilir; boylece transfer toplamlari sismez.
    """
    veritabani_hazirla()
    where, args = _kimlik_filtre(faaliyet, proje)
    if yil:
        where.append("substr(f.tarih,1,4)=?")
        args.append(f"{int(yil):04d}")
    if ay:
        where.append("substr(f.tarih,6,2)=?")
        args.append(f"{int(ay):02d}")
    kosul = (" AND " + " AND ".join(where)) if where else ""
    konsolide = faaliyet in (None, "", "tum", "touche_holding")
    # Yalnizca grup ici fislerin 131/331 satirlarini ele (nakit satirlari netlesir)
    ic_haric = ""
    if konsolide:
        ic_haric = (
            " AND NOT (s.hesap_kodu IN ('131','331') AND s.fis_id IN ("
            "SELECT kaynak_fis_id FROM sirketler_arasi_islemler "
            "UNION SELECT hedef_fis_id FROM sirketler_arasi_islemler))"
        )
    with baglanti() as con:
        rows = con.execute(
            "SELECT s.hesap_kodu, h.hesap_tipi, SUM(s.borc_kurus) b, SUM(s.alacak_kurus) a "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "JOIN hesaplar h ON h.kod=s.hesap_kodu WHERE 1=1" + kosul + ic_haric +
            " GROUP BY s.hesap_kodu, h.hesap_tipi",
            args,
        ).fetchall()
    nakit = alacak = borc = gelir = gider = 0
    for r in rows:
        net_b = r["b"] - r["a"]
        if r["hesap_kodu"] in NAKIT_HESAPLAR:
            nakit += net_b
        elif r["hesap_kodu"] in ALACAK_HESAPLAR:
            alacak += net_b
        elif r["hesap_kodu"] in BORC_HESAPLAR:
            borc += -net_b
        elif r["hesap_tipi"] == "GELIR":
            gelir += r["a"] - r["b"]
        elif r["hesap_tipi"] == "GIDER":
            gider += net_b
    return {"nakit_kurus": nakit, "alacak_kurus": alacak, "borc_kurus": borc,
            "ciro_kurus": gelir, "maliyet_kurus": gider,
            "brut_kar_kurus": gelir - gider, "konsolide": konsolide}


def _gunluk_gider(faaliyet=None, bugun=None, gun: int = 90) -> int:
    """Son `gun` gunun ortalama gunluk nakit cikisini kuruş olarak dondurur."""
    veritabani_hazirla()
    bugun = bugun or date.today()
    bas = bugun - timedelta(days=gun)
    where, args = _kimlik_filtre(faaliyet, None)
    where.append("f.tarih>=?")
    args.append(bas.isoformat())
    with baglanti() as con:
        cikis = con.execute(
            "SELECT COALESCE(SUM(s.alacak_kurus),0) t FROM fisler f "
            "JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "WHERE s.hesap_kodu IN (" + _in(NAKIT_HESAPLAR) + ") AND " + " AND ".join(where),
            [*NAKIT_HESAPLAR, *args],
        ).fetchone()["t"]
    return int(round(cikis / max(1, gun)))


def korunacak_nakit(faaliyet=None, bugun=None, rezerv_gun: int = 30) -> int:
    """Korunacak asgari nakdi (gunluk operasyonel gider x rezerv gunu) dondurur."""
    rezerv_gun = max(7, min(int(rezerv_gun or 30), 180))
    return _gunluk_gider(faaliyet, bugun) * rezerv_gun


def _nakit_bakiye(faaliyet=None, tarih=None) -> int:
    """Belirtilen tarihe kadar nakit hesap bakiyesi (kurus)."""
    veritabani_hazirla()
    where = _kimlik_filtre(faaliyet, None)[0] or ["1=1"]
    args = _kimlik_filtre(faaliyet, None)[1]
    if tarih is not None:
        where.append("f.tarih<=?")
        args.append(tarih.isoformat())
    with baglanti() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(s.borc_kurus-s.alacak_kurus),0) t FROM fisler f "
            "JOIN fis_satirlari s ON s.fis_id=f.fis_id WHERE s.hesap_kodu IN (" +
            _in(NAKIT_HESAPLAR) + ") AND " + " AND ".join(where),
            [*NAKIT_HESAPLAR, *args],
        ).fetchone()
    return int(row["t"])


def vadeli_ozet(faaliyet=None, bugun=None, gun: int = 30) -> dict:
    """Onumuzdeki `gun` gun icinde vadeli alacak (120) ve borc (320) toplami."""
    veritabani_hazirla()
    bugun = bugun or date.today()
    son = bugun + timedelta(days=max(1, int(gun)))
    where, args = _kimlik_filtre(faaliyet, None)
    with baglanti() as con:
        rows = con.execute(
            "SELECT f.vade, s.hesap_kodu, s.borc_kurus, s.alacak_kurus FROM fisler f "
            "JOIN fis_satirlari s ON s.fis_id=f.fis_id WHERE s.hesap_kodu IN ('120','320') "
            "AND TRIM(COALESCE(f.vade,''))<>'' AND " + " AND ".join(where or ["1=1"]),
            args,
        ).fetchall()
    giris = cikis = 0
    for r in rows:
        try:
            vade = date.fromisoformat(str(r["vade"])[:10])
        except ValueError:
            continue
        if not bugun <= vade <= son:
            continue
        if r["hesap_kodu"] == "120":
            giris += max(0, r["borc_kurus"] - r["alacak_kurus"])
        else:
            cikis += max(0, r["alacak_kurus"] - r["borc_kurus"])
    return {"beklenen_tahsilat_kurus": giris, "vadesi_gelen_odeme_kurus": cikis}


def karlilik(gelir_kurus: int, maliyet_kurus: int) -> dict:
    """Brut kar ve marj. Gelir sifirsa marj None (N/A) doner; boylece '%0'
    gibi yanlis bir basari gostergesi uretilmez."""
    brut = gelir_kurus - maliyet_kurus
    marj = round(brut / gelir_kurus * 100, 2) if gelir_kurus else None
    return {"brut_kar_kurus": brut, "brut_marj_yuzde": marj}


def marj_metin(marj_yuzde) -> str:
    """Marj yoksa (N/A) tire dondurur."""
    return "\u2014" if marj_yuzde is None else yuzde_metin(marj_yuzde)


def marj_riski(marj_yuzde, hedef_yuzde: float = 15.0) -> str:
    """Marj icin durum: healthy / warning / danger / na (marj yok)."""
    if marj_yuzde is None:
        return "na"
    marj = float(marj_yuzde)
    if marj < 0:
        return "danger"
    if marj < float(hedef_yuzde):
        return "warning"
    return "healthy"


def holding_kpi(faaliyet=None, profil=None, bugun=None) -> dict:
    """Holding/faaliyet seviyesinde CFO KPI paketi uretir."""
    profil = profil or {}
    rezerv_gun = int(profil.get("rezerv_gun", 30) or 30)
    ozet = bakiye_ozeti(faaliyet)
    korunacak = korunacak_nakit(faaliyet, bugun, rezerv_gun)
    vadeli = vadeli_ozet(faaliyet, bugun, 30)
    kar = karlilik(ozet["ciro_kurus"], ozet["maliyet_kurus"])
    nakit = ozet["nakit_kurus"]
    kullanilabilir = nakit - korunacak
    gecmis_nakit = _nakit_bakiye(faaliyet, (bugun or date.today()) - timedelta(days=30))
    nakit_trend = round((nakit - gecmis_nakit) / abs(gecmis_nakit) * 100, 1) if gecmis_nakit else 0.0
    isletme_sermayesi = nakit + ozet["alacak_kurus"] - ozet["borc_kurus"]
    yatirim_kapasitesi = max(
        0, nakit + vadeli["beklenen_tahsilat_kurus"] - korunacak - vadeli["vadesi_gelen_odeme_kurus"]
    )
    min13 = _min_13_hafta(faaliyet, bugun)
    kpi = {
        "faaliyet": faaliyet or "tum",
        "toplam_nakit_kurus": nakit,
        "korunacak_nakit_kurus": korunacak,
        "kullanilabilir_nakit_kurus": kullanilabilir,
        "toplam_alacak_kurus": ozet["alacak_kurus"],
        "toplam_borc_kurus": ozet["borc_kurus"],
        "net_isletme_sermayesi_kurus": isletme_sermayesi,
        "toplam_ciro_kurus": ozet["ciro_kurus"],
        "toplam_maliyet_kurus": ozet["maliyet_kurus"],
        "brut_kar_kurus": kar["brut_kar_kurus"],
        "brut_marj_yuzde": kar["brut_marj_yuzde"],
        "yatirim_kapasitesi_kurus": yatirim_kapasitesi,
        "beklenen_tahsilat_30_kurus": vadeli["beklenen_tahsilat_kurus"],
        "vadesi_gelen_odeme_30_kurus": vadeli["vadesi_gelen_odeme_kurus"],
        "min_13_hafta_nakit_kurus": min13,
        "nakit_trend_yuzde": nakit_trend,
        "marj_durum": marj_riski(kar["brut_marj_yuzde"], float(profil.get("hedef_marj", 15) or 15)),
        "rezerv_gun": rezerv_gun,
    }
    kpi["metin"] = {
        "toplam_nakit": tl_metin(nakit / 100),
        "kullanilabilir_nakit": tl_metin(kullanilabilir / 100),
        "korunacak_nakit": tl_metin(korunacak / 100),
        "toplam_alacak": tl_metin(ozet["alacak_kurus"] / 100),
        "toplam_borc": tl_metin(ozet["borc_kurus"] / 100),
        "net_isletme_sermayesi": tl_metin(isletme_sermayesi / 100),
        "brut_marj": yuzde_metin(kar["brut_marj_yuzde"]),
        "yatirim_kapasitesi": tl_metin(yatirim_kapasitesi / 100),
        "min_13_hafta": tl_metin(min13 / 100),
    }
    return kpi


def _min_13_hafta(faaliyet=None, bugun=None) -> int:
    """13 haftalik projeksiyondaki en dusuk kapanis nakitini (kurus) dondurur."""
    try:
        from nakit_13hafta import nakit_13_hafta

        analiz = nakit_13_hafta(faaliyet=faaliyet, bugun=bugun, baslangic_nakit=0)
        kapanislar = [h["kapanis_kurus"] for h in analiz["haftalar"]]
        return min(kapanislar) if kapanislar else 0
    except Exception:  # pragma: no cover - forecast modulu yoksa sessizce 0
        return 0


_BOYUT_TANIM = {
    "sirket": ("f.faaliyet_key", "LEFT JOIN faaliyetler fa ON fa.faaliyet_key=f.faaliyet_key", "fa.ad"),
    "faaliyet_alani": (
        "fa.alan_id",
        "LEFT JOIN faaliyetler fa ON fa.faaliyet_key=f.faaliyet_key "
        "LEFT JOIN faaliyet_alanlari a ON a.alan_id=fa.alan_id",
        "COALESCE(a.ad, fa.alan_id)",
    ),
    "proje": ("f.proje", "", "f.proje"),
    "is_kalemi": (
        "f.is_kalemi_id",
        "LEFT JOIN is_kalemleri ik ON ik.kalem_id=f.is_kalemi_id",
        "COALESCE(ik.is_kalemi, f.is_kalemi_id)",
    ),
}


def boyut_kirilimi(boyut: str = "sirket", faaliyet=None) -> list[dict]:
    """Secilen dimension bazinda nakit/alacak/borc/ciro/maliyet kirilimi uretir."""
    if boyut not in _BOYUT_TANIM:
        raise ValueError(f"Gecersiz boyut: {boyut}")
    veritabani_hazirla()
    anahtar, ek_join, etiket = _BOYUT_TANIM[boyut]
    where, args = _kimlik_filtre(faaliyet, None)
    if boyut in {"proje", "is_kalemi"}:
        where.append(f"TRIM(COALESCE({anahtar},''))<>''")
    kosul = (" AND " + " AND ".join(where)) if where else ""
    sorgu = (
        f"SELECT {anahtar} anahtar, {etiket} etiket_ad, "
        f"SUM(CASE WHEN h.hesap_tipi='GELIR' THEN s.alacak_kurus-s.borc_kurus ELSE 0 END) ciro, "
        f"SUM(CASE WHEN h.hesap_tipi='GIDER' THEN s.borc_kurus-s.alacak_kurus ELSE 0 END) maliyet, "
        f"SUM(CASE WHEN s.hesap_kodu IN ({_in(NAKIT_HESAPLAR)}) THEN s.borc_kurus-s.alacak_kurus ELSE 0 END) nakit, "
        f"SUM(CASE WHEN s.hesap_kodu IN ({_in(ALACAK_HESAPLAR)}) THEN s.borc_kurus-s.alacak_kurus ELSE 0 END) alacak, "
        f"SUM(CASE WHEN s.hesap_kodu IN ({_in(BORC_HESAPLAR)}) THEN s.alacak_kurus-s.borc_kurus ELSE 0 END) borc "
        f"FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id JOIN hesaplar h ON h.kod=s.hesap_kodu "
        f"{ek_join} WHERE 1=1{kosul} GROUP BY anahtar, etiket_ad ORDER BY ciro DESC"
    )
    parametreler = [*NAKIT_HESAPLAR, *ALACAK_HESAPLAR, *BORC_HESAPLAR, *args]
    with baglanti() as con:
        rows = con.execute(sorgu, parametreler).fetchall()
    sonuc = []
    for r in rows:
        kar = karlilik(r["ciro"], r["maliyet"])
        sonuc.append({
            "anahtar": r["anahtar"], "ad": r["etiket_ad"] or r["anahtar"],
            "nakit_kurus": r["nakit"], "alacak_kurus": r["alacak"], "borc_kurus": r["borc"],
            "ciro_kurus": r["ciro"], "maliyet_kurus": r["maliyet"],
            "brut_kar_kurus": kar["brut_kar_kurus"], "brut_marj_yuzde": kar["brut_marj_yuzde"],
            "marj_durum": marj_riski(kar["brut_marj_yuzde"]),
            "ciro_metin": tl_metin(r["ciro"] / 100), "maliyet_metin": tl_metin(r["maliyet"] / 100),
            "brut_kar_metin": tl_metin(kar["brut_kar_kurus"] / 100),
            "brut_marj_metin": yuzde_metin(kar["brut_marj_yuzde"]),
        })
    return sonuc


def proje_karlilik(faaliyet=None) -> list[dict]:
    return boyut_kirilimi("proje", faaliyet)


def is_kalemi_grid(faaliyet=None, proje=None) -> list[dict]:
    """Is kalemi bazinda butce/taahhut/gerceklesen/tahmin + ciro/marj grid'i."""
    veritabani_hazirla()
    where, args = ["1=1"], []
    if faaliyet and faaliyet not in {"tum", "touche_holding"}:
        where.append("k.faaliyet_key=?")
        args.append(faaliyet)
    if proje:
        where.append("k.proje=?")
        args.append(proje)
    with baglanti() as con:
        rows = con.execute(
            "SELECT k.*, (SELECT COALESCE(SUM(s.borc_kurus-s.alacak_kurus),0) FROM fisler f "
            "JOIN fis_satirlari s ON s.fis_id=f.fis_id JOIN hesaplar h ON h.kod=s.hesap_kodu "
            "WHERE f.is_kalemi_id=k.kalem_id AND h.hesap_tipi='GIDER') gercek_gider "
            "FROM is_kalemleri k WHERE " + " AND ".join(where) + " ORDER BY k.proje, k.maliyet_grubu, k.is_kalemi",
            args,
        ).fetchall()
    sonuc = []
    for r in rows:
        metraj = r["metraj_mikro"] / 1000
        ciro = int(round((r["metraj_mikro"] / 1000) * r["satis_birim_fiyati_kurus"]))
        maliyet_birim = int(round((r["metraj_mikro"] / 1000) * r["maliyet_birim_fiyati_kurus"]))
        gercek = max(0, int(r["gercek_gider"])) + int(r["gerceklesen_kurus"])
        tahmin = int(r["tahmin_kurus"]) or maliyet_birim
        kar = karlilik(ciro, tahmin)
        sonuc.append({
            "kalem_id": r["kalem_id"], "faaliyet": r["faaliyet_key"], "proje": r["proje"],
            "maliyet_grubu": r["maliyet_grubu"], "is_kalemi": r["is_kalemi"], "birim": r["birim"],
            "metraj": round(metraj, 3),
            "satis_birim_fiyati_kurus": r["satis_birim_fiyati_kurus"],
            "maliyet_birim_fiyati_kurus": r["maliyet_birim_fiyati_kurus"],
            "ciro_kurus": ciro, "butce_kurus": r["butce_kurus"], "taahhut_kurus": r["taahhut_kurus"],
            "gerceklesen_kurus": gercek, "tahmin_kurus": tahmin,
            "brut_kar_kurus": kar["brut_kar_kurus"], "brut_marj_yuzde": kar["brut_marj_yuzde"],
            "marj_durum": marj_riski(kar["brut_marj_yuzde"]),
            "ciro_metin": tl_metin(ciro / 100), "butce_metin": tl_metin(r["butce_kurus"] / 100),
            "gerceklesen_metin": tl_metin(gercek / 100), "tahmin_metin": tl_metin(tahmin / 100),
            "brut_kar_metin": tl_metin(kar["brut_kar_kurus"] / 100),
            "brut_marj_metin": yuzde_metin(kar["brut_marj_yuzde"]),
        })
    return sonuc



