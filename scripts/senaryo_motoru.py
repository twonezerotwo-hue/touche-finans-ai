# -*- coding: utf-8 -*-
"""Gelismis senaryo motoru.

'Karardan once etkisini gor' bolumunu; cok degiskenli, sonucu NEDEN'i ile
aciklayan ve 4 seviyeli (Guvenli / Sinirda / Riskli / Uygulanamaz) bir
senaryo hesaplayicisina donusturur. Senaryo surumleri (Base/Best/Worst/
Management Plan) varsayimlariyla birlikte saklanabilir.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from finans_motoru import bakiye_ozeti, holding_kpi
from money import tl_metin, yuzde_metin
from veri_katmani import baglanti, veritabani_hazirla

# (anahtar, etiket, tur) - tur: tutar / gun / yuzde
DEGISKENLER = [
    ("ek_tahsilat", "Ek tahsilat", "tutar"),
    ("tahsilat_gecikmesi", "Tahsilat gecikmesi", "gun"),
    ("ek_odeme", "Ek odeme", "tutar"),
    ("odeme_gecikmesi", "Odeme gecikmesi", "gun"),
    ("yeni_yatirim", "Yeni yatirim", "tutar"),
    ("yeni_kredi", "Yeni kredi", "tutar"),
    ("faiz_maliyeti", "Faiz maliyeti (aylik)", "tutar"),
    ("kur_degisimi", "Kur degisimi", "yuzde"),
    ("gelir_degisimi", "Gelir degisimi", "yuzde"),
    ("maliyet_artisi", "Maliyet artisi", "yuzde"),
    ("proje_gecikmesi", "Proje gecikmesi", "hafta"),
]
_ANAHTARLAR = {k: t for k, _, t in DEGISKENLER}


def _deger(degisiklikler, anahtar) -> float:
    try:
        return float(degisiklikler.get(anahtar, 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{anahtar} gecerli bir sayi olmali.") from exc


def _fx_pozisyon_kurus(faaliyet=None) -> int:
    """Yabanci para net pozisyonu (kabaca, TL kuruş)."""
    veritabani_hazirla()
    where = "" if not faaliyet or faaliyet in {"tum", "touche_holding"} else " AND f.faaliyet_key=?"
    args = () if not where else (faaliyet,)
    with baglanti() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(CASE WHEN s.hesap_kodu IN ('100','101','102','120','131','159') "
            "THEN s.borc_kurus-s.alacak_kurus ELSE 0 END),0) varlik, "
            "COALESCE(SUM(CASE WHEN s.hesap_kodu IN ('300','320','331','335','336') "
            "THEN s.alacak_kurus-s.borc_kurus ELSE 0 END),0) borc "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "WHERE f.para_birimi<>'TL'" + where,
            args,
        ).fetchone()
    return int(row["varlik"] - row["borc"])


def _seviye(senaryo_nakit: int, kullanilabilir: int, korunacak: int) -> tuple[str, str]:
    if senaryo_nakit < 0:
        return "Uygulanamaz", "danger"
    if senaryo_nakit < korunacak:
        return "Riskli", "danger"
    esik_ust = korunacak + max(1, int(korunacak * 0.1))
    if kullanilabilir > 0 and senaryo_nakit >= esik_ust:
        return "Guvenli", "healthy"
    return "Sinirda", "warning"


def senaryo_hesapla(faaliyet="tum", profil=None, degisiklikler=None) -> dict:
    """Degisken setine gore 4 seviyeli senaryo sonucu ve gerekce uretir."""
    degisiklikler = degisiklikler or {}
    degerler = {k: _deger(degisiklikler, k) for k in _ANAHTARLAR}
    kpi = holding_kpi(faaliyet, profil)
    nakit = kpi["toplam_nakit_kurus"]
    korunacak = kpi["korunacak_nakit_kurus"]
    tahsilat = kpi["beklenen_tahsilat_30_kurus"]
    odeme = kpi["vadesi_gelen_odeme_30_kurus"]
    ciro = kpi["toplam_ciro_kurus"]
    maliyet = kpi["toplam_maliyet_kurus"]
    fx = _fx_pozisyon_kurus(faaliyet)

    tahsilat_etki = round(tahsilat * max(0.0, 1 - degerler["tahsilat_gecikmesi"] / 30.0))
    odeme_etki = round(odeme * max(0.0, 1 - degerler["odeme_gecikmesi"] / 30.0))
    gelir_etki = round(ciro * degerler["gelir_degisimi"] / 100.0)
    maliyet_etki = round(maliyet * degerler["maliyet_artisi"] / 100.0)
    kur_etki = round(fx * degerler["kur_degisimi"] / 100.0)
    ek_tahsilat = int(round(degerler["ek_tahsilat"] * 100))
    ek_odeme = int(round(degerler["ek_odeme"] * 100))
    yatirim = int(round(degerler["yeni_yatirim"] * 100))
    kredi = int(round(degerler["yeni_kredi"] * 100))
    faiz = int(round(degerler["faiz_maliyeti"] * 100))
    gecikme = int(round(degerler["proje_gecikmesi"])) * max(0, korunacak // 13)

    girisler = tahsilat_etki + gelir_etki + ek_tahsilat + kredi + kur_etki
    cikislar = odeme_etki + maliyet_etki + ek_odeme + yatirim + faiz + gecikme
    senaryo_nakit = nakit + girisler - cikislar
    kullanilabilir = senaryo_nakit - korunacak
    seviye, risk = _seviye(senaryo_nakit, kullanilabilir, korunacak)

    adimlar = [
        {"etiket": "Mevcut nakit", "tutar_kurus": nakit, "isaret": "+",
         "metin": tl_metin(nakit / 100)},
        {"etiket": "Beklenen tahsilat (duzeltilmis)", "tutar_kurus": tahsilat_etki, "isaret": "+",
         "metin": tl_metin(tahsilat_etki / 100)},
        {"etiket": "Gelir degisimi etkisi", "tutar_kurus": gelir_etki, "isaret": "+",
         "metin": tl_metin(gelir_etki / 100)},
        {"etiket": "Ek tahsilat", "tutar_kurus": ek_tahsilat, "isaret": "+",
         "metin": tl_metin(ek_tahsilat / 100)},
        {"etiket": "Yeni kredi", "tutar_kurus": kredi, "isaret": "+",
         "metin": tl_metin(kredi / 100)},
        {"etiket": "Kur etkisi", "tutar_kurus": kur_etki, "isaret": "+",
         "metin": tl_metin(kur_etki / 100)},
        {"etiket": "Planlanan odeme (duzeltilmis)", "tutar_kurus": odeme_etki, "isaret": "-",
         "metin": tl_metin(odeme_etki / 100)},
        {"etiket": "Maliyet artisi etkisi", "tutar_kurus": maliyet_etki, "isaret": "-",
         "metin": tl_metin(maliyet_etki / 100)},
        {"etiket": "Ek odeme", "tutar_kurus": ek_odeme, "isaret": "-",
         "metin": tl_metin(ek_odeme / 100)},
        {"etiket": "Yeni yatirim", "tutar_kurus": yatirim, "isaret": "-",
         "metin": tl_metin(yatirim / 100)},
        {"etiket": "Faiz maliyeti", "tutar_kurus": faiz, "isaret": "-",
         "metin": tl_metin(faiz / 100)},
        {"etiket": "Proje gecikmesi etkisi", "tutar_kurus": gecikme, "isaret": "-",
         "metin": tl_metin(gecikme / 100)},
        {"etiket": "Korunacak minimum nakit", "tutar_kurus": korunacak, "isaret": "-",
         "metin": tl_metin(korunacak / 100)},
    ]
    return {
        "faaliyet": faaliyet, "girdiler": degerler, "seviye": seviye, "risk": risk,
        "mevcut_nakit_kurus": nakit, "senaryo_nakit_kurus": senaryo_nakit,
        "korunacak_nakit_kurus": korunacak, "kullanilabilir_nakit_kurus": kullanilabilir,
        "girisler_kurus": girisler, "cikislar_kurus": cikislar, "adimlar": adimlar,
        "metin": {"mevcut_nakit": tl_metin(nakit / 100), "senaryo_nakit": tl_metin(senaryo_nakit / 100),
                  "korunacak": tl_metin(korunacak / 100),
                  "kullanilabilir": tl_metin(kullanilabilir / 100),
                  "girisler": tl_metin(girisler / 100), "cikislar": tl_metin(cikislar / 100)},
    }


def senaryo_kaydet(ad: str, tip: str = "BASE", faaliyet="tum", profil=None,
                   degisiklikler=None, aktor="sistem") -> dict:
    """Senaryoyu varsayimlari ve sonucuyla saklar (surumleme)."""
    tip = str(tip or "BASE").upper()
    if tip not in {"BASE", "BEST", "WORST", "MGMT"}:
        raise ValueError("Senaryo tipi BASE, BEST, WORST veya MGMT olmali.")
    ad = str(ad or "").strip()[:120]
    if not ad:
        raise ValueError("Senaryo adi gerekli.")
    sonuc = senaryo_hesapla(faaliyet, profil, degisiklikler)
    senaryo_id = f"SCN-{uuid.uuid4().hex[:12].upper()}"
    simdi = datetime.now().isoformat(timespec="seconds")
    veritabani_hazirla()
    with baglanti() as con:
        con.execute(
            "INSERT INTO senaryolar(senaryo_id,ad,tip,varsayimlar,sonuc,olusturan,olusturma,guncelleme) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (senaryo_id, ad, tip, json.dumps(sonuc["girdiler"], ensure_ascii=False),
             json.dumps(sonuc, ensure_ascii=False), str(aktor), simdi, simdi),
        )
    return {"senaryo_id": senaryo_id, "ad": ad, "tip": tip, "sonuc": sonuc}


def senaryolari_listele(limit: int = 50) -> list[dict]:
    veritabani_hazirla()
    with baglanti() as con:
        rows = con.execute(
            "SELECT senaryo_id,ad,tip,varsayimlar,sonuc,olusturan,olusturma FROM senaryolar "
            "ORDER BY olusturma DESC LIMIT ?", (max(1, min(int(limit), 500)),),
        ).fetchall()
    sonuc = []
    for r in rows:
        veri = json.loads(r["sonuc"] or "{}")
        sonuc.append({"senaryo_id": r["senaryo_id"], "ad": r["ad"], "tip": r["tip"],
                      "varsayimlar": json.loads(r["varsayimlar"] or "{}"),
                      "seviye": veri.get("seviye"), "senaryo_nakit_kurus": veri.get("senaryo_nakit_kurus"),
                      "kullanilabilir_nakit_kurus": veri.get("kullanilabilir_nakit_kurus"),
                      "olusturan": r["olusturan"], "olusturma": r["olusturma"]})
    return sonuc


def senaryo_karsilastir() -> dict:
    """Kayitli senaryolari (Base/Best/Worst/Mgmt) yan yana karsilastirir."""
    kayitlar = senaryolari_listele(200)
    return {"senaryolar": kayitlar, "adet": len(kayitlar)}


