# -*- coding: utf-8 -*-
"""Holding konsolidasyonu ve kurumsal finans kontrolleri.

Bu katman mevcut merkezi defteri değiştirmeden; ortak hesap planı eşleme,
şirketler arası çift kayıt, eliminasyon, kur değerleme, yaşlandırma,
bütçe sapması, banka mutabakatı ve onay matrisini aynı SQLite verisine bağlar.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from finans_cekirdegi import (
    _baglanti, _donem_acik_mi, _kurus, _simdi, _tarih,
    veritabanini_hazirla,
)
from guvenlik import audit_yaz, belge_sha256


ROLLER = {"ADMIN", "FINANS", "ONAYCI", "IZLEYICI"}
PARASAL_HESAPLAR = {"100", "101", "102", "120", "131", "159", "300", "320", "331", "335", "336"}


def _kimlik(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


def _faaliyet_var(con, faaliyet):
    return bool(con.execute(
        "SELECT 1 FROM faaliyetler WHERE faaliyet_key=? AND aktif=1", (faaliyet,)
    ).fetchone())


def _fis_ekle(con, *, fis_id, belge_id, faaliyet, tarih, satirlar, aciklama,
              aktor, proje="", cari="", kaynak="kurumsal_finans", pb="TL",
              kur_mikro=1_000_000, sira=1):
    gun = _tarih(tarih)
    if not _faaliyet_var(con, faaliyet):
        raise ValueError(f"Faaliyet bulunamadı: {faaliyet}")
    if not _donem_acik_mi(con, gun):
        raise ValueError(f"{gun:%Y-%m} dönemi kapalı; kayıt yapılamaz.")
    borc = sum(int(x.get("borc", 0)) for x in satirlar)
    alacak = sum(int(x.get("alacak", 0)) for x in satirlar)
    if borc <= 0 or borc != alacak:
        raise ValueError("Muhasebe fişi dengeli ve sıfırdan büyük olmalı.")
    hesaplar = {x["hesap"] for x in satirlar}
    bulunan = {r[0] for r in con.execute(
        f"SELECT kod FROM hesaplar WHERE kod IN ({','.join('?' for _ in hesaplar)}) AND aktif=1",
        tuple(hesaplar),
    ).fetchall()}
    if bulunan != hesaplar:
        raise ValueError(f"Geçersiz hesap kodu: {', '.join(sorted(hesaplar - bulunan))}")
    con.execute(
        "INSERT INTO fisler(fis_id,belge_id,kayit_sira,faaliyet_key,tarih,proje,cari,aciklama,"
        "para_birimi,kur_mikro,kaynak,olusturan,onaylayan,olusturma_zamani) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fis_id, belge_id, sira, faaliyet, gun.isoformat(), str(proje or ""), str(cari or ""),
         str(aciklama or ""), str(pb or "TL").upper(), int(kur_mikro), str(kaynak),
         str(aktor), str(aktor), _simdi()),
    )
    con.executemany(
        "INSERT INTO fis_satirlari(fis_id,satir_no,hesap_kodu,borc_kurus,alacak_kurus,aciklama) "
        "VALUES(?,?,?,?,?,?)",
        [(fis_id, i, x["hesap"], int(x.get("borc", 0)), int(x.get("alacak", 0)),
          str(x.get("aciklama") or aciklama or "")) for i, x in enumerate(satirlar, 1)],
    )
    return fis_id


def hesap_eslemesi_kaydet(faaliyet, yerel_hesap, grup_hesap, aktor="sistem", aciklama=""):
    """Faaliyetin yerel hesap kodunu holding ortak hesap planına bağlar."""
    veritabanini_hazirla()
    faaliyet, yerel_hesap, grup_hesap = str(faaliyet), str(yerel_hesap), str(grup_hesap)
    with _baglanti() as con:
        if not _faaliyet_var(con, faaliyet):
            raise ValueError("Faaliyet bulunamadı.")
        if not con.execute("SELECT 1 FROM hesaplar WHERE kod=? AND aktif=1", (grup_hesap,)).fetchone():
            raise ValueError("Grup hesap kodu bulunamadı.")
        con.execute(
            "INSERT INTO hesap_eslemeleri(faaliyet_key,yerel_hesap_kodu,grup_hesap_kodu,aciklama,guncelleyen,guncelleme) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(faaliyet_key,yerel_hesap_kodu) DO UPDATE SET "
            "grup_hesap_kodu=excluded.grup_hesap_kodu,aciklama=excluded.aciklama,"
            "guncelleyen=excluded.guncelleyen,guncelleme=excluded.guncelleme",
            (faaliyet, yerel_hesap, grup_hesap, str(aciklama), str(aktor), _simdi()),
        )
    audit_yaz("hesap_eslemesi_guncellendi", faaliyet=faaliyet, yerel=yerel_hesap,
               grup=grup_hesap, aktor=str(aktor))
    return {"faaliyet": faaliyet, "yerel_hesap": yerel_hesap, "grup_hesap": grup_hesap}


def hesap_eslemelerini_listele(faaliyet=None):
    veritabanini_hazirla()
    with _baglanti() as con:
        rows = con.execute(
            "SELECT e.faaliyet_key,e.yerel_hesap_kodu,e.grup_hesap_kodu,h.ad,e.aciklama "
            "FROM hesap_eslemeleri e JOIN hesaplar h ON h.kod=e.grup_hesap_kodu "
            + ("WHERE e.faaliyet_key=? " if faaliyet else "")
            + "ORDER BY e.faaliyet_key,e.yerel_hesap_kodu",
            (faaliyet,) if faaliyet else (),
        ).fetchall()
    return [dict(r) for r in rows]


def sirketler_arasi_transfer(kaynak_faaliyet, hedef_faaliyet, tarih, tutar,
                             aciklama, aktor="sistem", proje="", pb="TL", kur=1):
    """Tek işlemle iki faaliyette karşılıklı, dengeli şirketler arası kayıt oluşturur."""
    if kaynak_faaliyet == hedef_faaliyet:
        raise ValueError("Kaynak ve hedef faaliyet farklı olmalı.")
    tutar_kurus = _kurus(tutar)
    if tutar_kurus <= 0:
        raise ValueError("Transfer tutarı sıfırdan büyük olmalı.")
    kur_mikro = max(1, int(float(kur) * 1_000_000))
    tl_kurus = int(round(tutar_kurus * (kur_mikro / 1_000_000))) if str(pb).upper() != "TL" else tutar_kurus
    islem_id = _kimlik("IC")
    kaynak_fis, hedef_fis = f"JRN-{islem_id}-SRC", f"JRN-{islem_id}-DST"
    with _baglanti() as con:
        if not _faaliyet_var(con, kaynak_faaliyet) or not _faaliyet_var(con, hedef_faaliyet):
            raise ValueError("Kaynak veya hedef faaliyet bulunamadı.")
        _fis_ekle(
            con, fis_id=kaynak_fis, belge_id=f"{islem_id}-SRC", faaliyet=kaynak_faaliyet,
            tarih=tarih, proje=proje, cari=hedef_faaliyet, aciklama=aciklama, aktor=aktor,
            pb=pb, kur_mikro=kur_mikro,
            satirlar=[
                {"hesap": "131", "borc": tl_kurus, "alacak": 0, "aciklama": "Grup şirketinden alacak"},
                {"hesap": "102", "borc": 0, "alacak": tl_kurus, "aciklama": "Banka çıkışı"},
            ],
        )
        _fis_ekle(
            con, fis_id=hedef_fis, belge_id=f"{islem_id}-DST", faaliyet=hedef_faaliyet,
            tarih=tarih, proje=proje, cari=kaynak_faaliyet, aciklama=aciklama, aktor=aktor,
            pb=pb, kur_mikro=kur_mikro,
            satirlar=[
                {"hesap": "102", "borc": tl_kurus, "alacak": 0, "aciklama": "Banka girişi"},
                {"hesap": "331", "borc": 0, "alacak": tl_kurus, "aciklama": "Grup şirketine borç"},
            ],
        )
        con.execute(
            "INSERT INTO sirketler_arasi_islemler VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (islem_id, kaynak_faaliyet, hedef_faaliyet, kaynak_fis, hedef_fis,
             _tarih(tarih).isoformat(), tl_kurus, str(pb).upper(), str(aciklama), "AKTIF",
             str(aktor), _simdi()),
        )
    audit_yaz("sirketler_arasi_transfer", islem_id=islem_id, kaynak=kaynak_faaliyet,
               hedef=hedef_faaliyet, tutar=tl_kurus / 100, aktor=str(aktor))
    return {"islem_id": islem_id, "kaynak_fis": kaynak_fis, "hedef_fis": hedef_fis,
            "tutar_tl": tl_kurus / 100}


def konsolide_mizan(baslangic, bitis, eliminasyon=True):
    """Ortak hesap planında holding mizanı; grup içi 131/331 bakiyelerini elimine eder."""
    veritabanini_hazirla()
    bas, son = _tarih(baslangic), _tarih(bitis)
    with _baglanti() as con:
        rows = con.execute(
            "SELECT COALESCE(e.grup_hesap_kodu,s.hesap_kodu) hesap,h.ad,h.hesap_tipi,"
            "SUM(s.borc_kurus) borc,SUM(s.alacak_kurus) alacak,"
            "SUM(CASE WHEN ic.islem_id IS NOT NULL AND s.hesap_kodu IN ('131','331') THEN s.borc_kurus ELSE 0 END) elim_borc,"
            "SUM(CASE WHEN ic.islem_id IS NOT NULL AND s.hesap_kodu IN ('131','331') THEN s.alacak_kurus ELSE 0 END) elim_alacak "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "LEFT JOIN hesap_eslemeleri e ON e.faaliyet_key=f.faaliyet_key AND e.yerel_hesap_kodu=s.hesap_kodu "
            "JOIN hesaplar h ON h.kod=COALESCE(e.grup_hesap_kodu,s.hesap_kodu) "
            "LEFT JOIN sirketler_arasi_islemler ic ON ic.durum='AKTIF' AND (ic.kaynak_fis_id=f.fis_id OR ic.hedef_fis_id=f.fis_id) "
            "WHERE f.tarih BETWEEN ? AND ? GROUP BY hesap,h.ad,h.hesap_tipi ORDER BY hesap",
            (bas.isoformat(), son.isoformat()),
        ).fetchall()
    sonuc = []
    for r in rows:
        eb, ea = (r["elim_borc"], r["elim_alacak"]) if eliminasyon else (0, 0)
        sonuc.append({
            "hesap": r["hesap"], "ad": r["ad"], "hesap_tipi": r["hesap_tipi"],
            "ham_borc": r["borc"] / 100, "ham_alacak": r["alacak"] / 100,
            "eliminasyon_borc": eb / 100, "eliminasyon_alacak": ea / 100,
            "borc": (r["borc"] - eb) / 100, "alacak": (r["alacak"] - ea) / 100,
            "bakiye": ((r["borc"] - eb) - (r["alacak"] - ea)) / 100,
        })
    return sonuc


def sirketler_arasi_mutabakat(baslangic=None, bitis=None):
    veritabanini_hazirla()
    where, args = ["ic.durum='AKTIF'"], []
    if baslangic:
        where.append("ic.tarih>=?"); args.append(_tarih(baslangic).isoformat())
    if bitis:
        where.append("ic.tarih<=?"); args.append(_tarih(bitis).isoformat())
    with _baglanti() as con:
        rows = con.execute(
            "SELECT ic.*,"
            "COALESCE((SELECT SUM(borc_kurus-alacak_kurus) FROM fis_satirlari WHERE fis_id=ic.kaynak_fis_id AND hesap_kodu='131'),0) kaynak_alacak,"
            "COALESCE((SELECT SUM(alacak_kurus-borc_kurus) FROM fis_satirlari WHERE fis_id=ic.hedef_fis_id AND hesap_kodu='331'),0) hedef_borc "
            "FROM sirketler_arasi_islemler ic WHERE " + " AND ".join(where) + " ORDER BY ic.tarih DESC",
            args,
        ).fetchall()
    return [{
        "islem_id": r["islem_id"], "kaynak": r["kaynak_faaliyet"], "hedef": r["hedef_faaliyet"],
        "tarih": r["tarih"], "tutar": r["tutar_kurus"] / 100,
        "fark": (r["kaynak_alacak"] - r["hedef_borc"]) / 100,
        "durum": "UYUMLU" if r["kaynak_alacak"] == r["hedef_borc"] else "FARKLI",
    } for r in rows]


def kur_kaydet(tarih, para_birimi, kur, aktor="sistem", kaynak="manuel"):
    pb = str(para_birimi or "").upper().strip()
    if pb == "TL" or len(pb) != 3:
        raise ValueError("TL dışındaki üç harfli para birimi gerekli.")
    kur_mikro = int(float(kur) * 1_000_000)
    if kur_mikro <= 0:
        raise ValueError("Kur sıfırdan büyük olmalı.")
    veritabanini_hazirla()
    with _baglanti() as con:
        con.execute(
            "INSERT INTO kurlar VALUES(?,?,?,?,?,?) ON CONFLICT(tarih,para_birimi) DO UPDATE SET "
            "kur_mikro=excluded.kur_mikro,kaynak=excluded.kaynak,guncelleyen=excluded.guncelleyen,guncelleme=excluded.guncelleme",
            (_tarih(tarih).isoformat(), pb, kur_mikro, str(kaynak), str(aktor), _simdi()),
        )
    audit_yaz("kur_guncellendi", tarih=_tarih(tarih).isoformat(), para_birimi=pb,
               kur=kur_mikro / 1_000_000, kaynak=kaynak, aktor=str(aktor))
    return {"tarih": _tarih(tarih).isoformat(), "para_birimi": pb, "kur": kur_mikro / 1_000_000}


def kurlari_listele(limit=100):
    veritabanini_hazirla()
    with _baglanti() as con:
        rows = con.execute(
            "SELECT tarih,para_birimi,kur_mikro,kaynak,guncelleyen,guncelleme FROM kurlar "
            "ORDER BY tarih DESC,para_birimi LIMIT ?", (max(1, min(int(limit), 1000)),)
        ).fetchall()
    return [{**dict(r), "kur": r["kur_mikro"] / 1_000_000} for r in rows]


def kur_degerleme(tarih, aktor="sistem"):
    """Yabancı para parasal hesaplarını kayıtlı dönem sonu kuruyla değerler."""
    veritabanini_hazirla()
    gun = _tarih(tarih)
    donem = gun.strftime("%Y%m")
    with _baglanti() as con:
        if not _donem_acik_mi(con, gun):
            raise ValueError(f"{gun:%Y-%m} dönemi kapalı; değerleme yapılamaz.")
        rows = con.execute(
            "SELECT f.faaliyet_key,f.para_birimi,s.hesap_kodu,h.hesap_tipi,"
            "SUM(CASE WHEN h.hesap_tipi='VARLIK' THEN s.borc_kurus-s.alacak_kurus ELSE s.alacak_kurus-s.borc_kurus END) tl_bakiye,"
            "SUM((CASE WHEN h.hesap_tipi='VARLIK' THEN s.borc_kurus-s.alacak_kurus ELSE s.alacak_kurus-s.borc_kurus END)*1000000.0/f.kur_mikro) doviz_kurus "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id JOIN hesaplar h ON h.kod=s.hesap_kodu "
            "WHERE f.tarih<=? AND f.para_birimi<>'TL' AND s.hesap_kodu IN ('100','101','102','120','131','159','300','320','331','335','336') "
            "GROUP BY f.faaliyet_key,f.para_birimi,s.hesap_kodu,h.hesap_tipi HAVING tl_bakiye<>0",
            (gun.isoformat(),),
        ).fetchall()
        olusan, atlanan = [], []
        for r in rows:
            kur = con.execute(
                "SELECT kur_mikro FROM kurlar WHERE para_birimi=? AND tarih<=? ORDER BY tarih DESC LIMIT 1",
                (r["para_birimi"], gun.isoformat()),
            ).fetchone()
            if not kur:
                atlanan.append({"para_birimi": r["para_birimi"], "sebep": "Kur bulunamadı"})
                continue
            yeni_tl = int(round(float(r["doviz_kurus"]) * kur["kur_mikro"] / 1_000_000))
            fark = yeni_tl - int(r["tl_bakiye"])
            if fark == 0:
                continue
            belge_id = f"FX-{donem}-{r['faaliyet_key']}-{r['para_birimi']}-{r['hesap_kodu']}"
            fis_id = f"JRN-{belge_id}-001"
            if con.execute("SELECT 1 FROM fisler WHERE fis_id=?", (fis_id,)).fetchone():
                atlanan.append({"fis_id": fis_id, "sebep": "Daha önce değerlendi"})
                continue
            varlik = r["hesap_tipi"] == "VARLIK"
            kazanc = (fark > 0 and varlik) or (fark < 0 and not varlik)
            tutar = abs(fark)
            if kazanc:
                satirlar = [
                    {"hesap": r["hesap_kodu"], "borc": tutar, "alacak": 0},
                    {"hesap": "646", "borc": 0, "alacak": tutar},
                ]
            else:
                satirlar = [
                    {"hesap": "656", "borc": tutar, "alacak": 0},
                    {"hesap": r["hesap_kodu"], "borc": 0, "alacak": tutar},
                ]
            _fis_ekle(con, fis_id=fis_id, belge_id=belge_id, faaliyet=r["faaliyet_key"],
                      tarih=gun, satirlar=satirlar, aciklama=f"{r['para_birimi']} dönem sonu kur değerlemesi",
                      aktor=aktor, kaynak="kur_degerleme")
            olusan.append({"fis_id": fis_id, "faaliyet": r["faaliyet_key"],
                           "para_birimi": r["para_birimi"], "hesap": r["hesap_kodu"], "fark": fark / 100})
    audit_yaz("kur_degerleme", tarih=gun.isoformat(), olusan=len(olusan), atlanan=len(atlanan), aktor=str(aktor))
    return {"olusan": olusan, "atlanan": atlanan}


def cari_yaslandirma(hesap_kodu="120", faaliyet=None, rapor_tarihi=None):
    """Alacak veya borç hareketlerini cari bazında FIFO kapatıp yaşlandırır."""
    if str(hesap_kodu) not in {"120", "320"}:
        raise ValueError("Yaşlandırma hesabı 120 veya 320 olmalı.")
    veritabanini_hazirla()
    bugun = _tarih(rapor_tarihi) if rapor_tarihi else date.today()
    where, args = ["s.hesap_kodu=?", "f.tarih<=?", "TRIM(COALESCE(f.cari,''))<>''"], [str(hesap_kodu), bugun.isoformat()]
    if faaliyet:
        where.append("f.faaliyet_key=?"); args.append(faaliyet)
    with _baglanti() as con:
        rows = con.execute(
            "SELECT f.faaliyet_key,f.cari,f.tarih,f.vade,f.fis_id,s.borc_kurus,s.alacak_kurus "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id WHERE " + " AND ".join(where) +
            " ORDER BY f.faaliyet_key,f.cari,f.tarih,f.fis_id",
            args,
        ).fetchall()
    gruplar = {}
    for r in rows:
        key = (r["faaliyet_key"], r["cari"])
        g = gruplar.setdefault(key, {"aciklar": [], "avans": 0})
        delta = (r["borc_kurus"] - r["alacak_kurus"]) if hesap_kodu == "120" else (r["alacak_kurus"] - r["borc_kurus"])
        if delta > 0:
            if g["avans"]:
                kullan = min(delta, g["avans"])
                delta -= kullan
                g["avans"] -= kullan
            if delta <= 0:
                continue
            try:
                vade = _tarih(r["vade"]) if str(r["vade"] or "").strip() else _tarih(r["tarih"])
            except ValueError:
                vade = _tarih(r["tarih"])
            g["aciklar"].append({"kalan": delta, "vade": vade, "fis_id": r["fis_id"]})
        elif delta < 0:
            kapama = -delta
            for acik in g["aciklar"]:
                kullan = min(acik["kalan"], kapama)
                acik["kalan"] -= kullan; kapama -= kullan
                if kapama == 0:
                    break
            g["avans"] += kapama
    sonuc = []
    for (faaliyet_key, cari), g in gruplar.items():
        buckets = {"vadesi_gelmemis": 0, "1_30": 0, "31_60": 0, "61_90": 0, "90_ustu": 0}
        for acik in g["aciklar"]:
            if acik["kalan"] <= 0:
                continue
            gun = (bugun - acik["vade"]).days
            alan = "vadesi_gelmemis" if gun <= 0 else "1_30" if gun <= 30 else "31_60" if gun <= 60 else "61_90" if gun <= 90 else "90_ustu"
            buckets[alan] += acik["kalan"]
        toplam = sum(buckets.values())
        if toplam or g["avans"]:
            sonuc.append({"faaliyet": faaliyet_key, "cari": cari,
                          **{k: v / 100 for k, v in buckets.items()},
                          "toplam": toplam / 100, "avans": g["avans"] / 100})
    return sorted(sonuc, key=lambda x: x["toplam"], reverse=True)


def butce_sapma_raporu(faaliyet, baslangic, bitis, proje=None):
    veritabanini_hazirla()
    bas, son = _tarih(baslangic), _tarih(bitis)
    where, args = ["date(printf('%04d-%02d-01',b.yil,b.ay)) BETWEEN ? AND ?"], [bas.replace(day=1).isoformat(), son.replace(day=1).isoformat()]
    if faaliyet and faaliyet not in {"tum", "touche_holding"}:
        where.append("b.faaliyet_key=?"); args.append(faaliyet)
    if proje is not None:
        where.append("b.proje=?"); args.append(str(proje))
    with _baglanti() as con:
        rows = con.execute(
            "SELECT b.faaliyet_key,b.hesap_kodu,h.ad,h.hesap_tipi,b.proje,SUM(b.tutar_kurus) butce,"
            "COALESCE((SELECT SUM(CASE WHEN h.hesap_tipi='GELIR' THEN s.alacak_kurus-s.borc_kurus ELSE s.borc_kurus-s.alacak_kurus END) "
            "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id WHERE f.faaliyet_key=b.faaliyet_key "
            "AND f.proje=b.proje AND s.hesap_kodu=b.hesap_kodu AND f.tarih BETWEEN ? AND ?),0) gerceklesen "
            "FROM butceler b JOIN hesaplar h ON h.kod=b.hesap_kodu WHERE " + " AND ".join(where) +
            " GROUP BY b.faaliyet_key,b.hesap_kodu,h.ad,h.hesap_tipi,b.proje ORDER BY b.faaliyet_key,b.hesap_kodu,b.proje",
            [bas.isoformat(), son.isoformat(), *args],
        ).fetchall()
    kalemler = []
    for r in rows:
        fark = r["gerceklesen"] - r["butce"]
        oran = (fark / abs(r["butce"]) * 100) if r["butce"] else (100 if r["gerceklesen"] else 0)
        olumsuz = fark > 0 if r["hesap_tipi"] != "GELIR" else fark < 0
        kalemler.append({"faaliyet": r["faaliyet_key"], "hesap": r["hesap_kodu"], "ad": r["ad"], "proje": r["proje"],
                         "butce": r["butce"] / 100, "gerceklesen": r["gerceklesen"] / 100,
                         "sapma": fark / 100, "sapma_orani": round(oran, 2),
                         "durum": "OLUMSUZ" if olumsuz else "OLUMLU"})
    return {"kalemler": kalemler, "toplam_butce": sum(x["butce"] for x in kalemler),
            "toplam_gerceklesen": sum(x["gerceklesen"] for x in kalemler),
            "olumsuz_kalem": sum(1 for x in kalemler if x["durum"] == "OLUMSUZ")}


def onay_kurali_kaydet(kural_id, faaliyet, min_tutar, max_tutar, gerekli_onay,
                       roller, ayri_hazirlayan, aktor="sistem"):
    roller = sorted({str(x).upper() for x in (roller or [])})
    if not roller or not set(roller) <= ROLLER:
        raise ValueError("Geçerli en az bir onay rolü gerekli.")
    gerekli = int(gerekli_onay)
    if gerekli < 1 or gerekli > 10:
        raise ValueError("Gerekli onay sayısı 1-10 arasında olmalı.")
    min_kurus = _kurus(min_tutar)
    max_kurus = None if max_tutar in (None, "") else _kurus(max_tutar)
    if max_kurus is not None and max_kurus < min_kurus:
        raise ValueError("Azami tutar asgari tutardan küçük olamaz.")
    veritabanini_hazirla()
    with _baglanti() as con:
        if faaliyet != "*" and not _faaliyet_var(con, faaliyet):
            raise ValueError("Faaliyet bulunamadı.")
        con.execute(
            "INSERT INTO onay_kurallari VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kural_id) DO UPDATE SET "
            "faaliyet_key=excluded.faaliyet_key,min_kurus=excluded.min_kurus,max_kurus=excluded.max_kurus,"
            "gerekli_onay=excluded.gerekli_onay,gerekli_roller=excluded.gerekli_roller,"
            "ayri_hazirlayan=excluded.ayri_hazirlayan,aktif=1,guncelleyen=excluded.guncelleyen,guncelleme=excluded.guncelleme",
            (str(kural_id), str(faaliyet), min_kurus, max_kurus, gerekli,
             json.dumps(roller, ensure_ascii=False), 1 if ayri_hazirlayan else 0, 1, str(aktor), _simdi()),
        )
    audit_yaz("onay_kurali_guncellendi", kural_id=kural_id, faaliyet=faaliyet,
               gerekli_onay=gerekli, roller=roller, aktor=str(aktor))


def onay_kurali_bul(faaliyet, toplam_tl):
    veritabanini_hazirla()
    tutar = _kurus(toplam_tl)
    with _baglanti() as con:
        row = con.execute(
            "SELECT * FROM onay_kurallari WHERE aktif=1 AND faaliyet_key IN (?, '*') "
            "AND min_kurus<=? AND (max_kurus IS NULL OR max_kurus>=?) "
            "ORDER BY CASE WHEN faaliyet_key=? THEN 0 ELSE 1 END,min_kurus DESC LIMIT 1",
            (faaliyet, tutar, tutar, faaliyet),
        ).fetchone()
    if not row:
        raise ValueError("Bu işlem için onay kuralı bulunamadı.")
    sonuc = dict(row); sonuc["gerekli_roller"] = json.loads(sonuc["gerekli_roller"] or "[]")
    return sonuc


def onay_kurallarini_listele():
    veritabanini_hazirla()
    with _baglanti() as con:
        rows = con.execute("SELECT * FROM onay_kurallari WHERE aktif=1 ORDER BY faaliyet_key,min_kurus").fetchall()
    sonuc = []
    for row in rows:
        item = dict(row)
        item["min_tutar"] = item.pop("min_kurus") / 100
        item["max_tutar"] = None if item["max_kurus"] is None else item.pop("max_kurus") / 100
        item.pop("max_kurus", None)
        item["gerekli_roller"] = json.loads(item["gerekli_roller"] or "[]")
        item["ayri_hazirlayan"] = bool(item["ayri_hazirlayan"])
        sonuc.append(item)
    return sonuc


def onay_karari_ekle(belge_id, faaliyet, toplam_tl, aktor, rol="ONAYCI", hazirlayan=None, karar="ONAY"):
    rol, karar = str(rol).upper(), str(karar).upper()
    if rol not in ROLLER or karar not in {"ONAY", "RET"}:
        raise ValueError("Geçersiz rol veya karar.")
    kural = onay_kurali_bul(faaliyet, toplam_tl)
    if rol not in kural["gerekli_roller"] and rol != "ADMIN":
        raise PermissionError("Rolünüz bu tutarı onaylamaya yetkili değil.")
    if kural["ayri_hazirlayan"] and hazirlayan and str(hazirlayan) == str(aktor):
        raise PermissionError("Hazırlayan kullanıcı kendi işlemini onaylayamaz.")
    with _baglanti() as con:
        con.execute("INSERT OR REPLACE INTO onay_kararlari VALUES(?,?,?,?,?)",
                    (str(belge_id), str(aktor), rol, karar, _simdi()))
        rows = con.execute("SELECT aktor,rol,karar,zaman FROM onay_kararlari WHERE belge_id=?", (str(belge_id),)).fetchall()
    ret = any(r["karar"] == "RET" for r in rows)
    onay = sum(1 for r in rows if r["karar"] == "ONAY")
    sonuc = {"kural_id": kural["kural_id"], "gerekli": kural["gerekli_onay"], "mevcut": onay,
             "reddedildi": ret, "tamam": not ret and onay >= kural["gerekli_onay"],
             "kararlar": [dict(r) for r in rows]}
    audit_yaz("onay_karari", belge_id=belge_id, aktor=str(aktor), rol=rol, karar=karar,
               tamam=sonuc["tamam"])
    return sonuc


def onay_kararlari_temizle(belge_id, aktor="sistem", neden="taslak_duzeltildi"):
    """İçeriği değişen bir taslağın önceki kararlarını geçersiz kılar."""
    veritabanini_hazirla()
    with _baglanti() as con:
        adet = con.execute(
            "SELECT COUNT(*) FROM onay_kararlari WHERE belge_id=?", (str(belge_id),)
        ).fetchone()[0]
        con.execute("DELETE FROM onay_kararlari WHERE belge_id=?", (str(belge_id),))
    audit_yaz("onay_kararlari_temizlendi", belge_id=str(belge_id), aktor=str(aktor),
               neden=str(neden), silinen=int(adet))
    return int(adet)


def banka_mutabakat_adaylari(hareket_id, limit=5):
    veritabanini_hazirla()
    with _baglanti() as con:
        h = con.execute("SELECT * FROM banka_hareketleri WHERE hareket_id=?", (hareket_id,)).fetchone()
        if not h:
            raise ValueError("Banka hareketi bulunamadı.")
        rows = con.execute(
            "SELECT DISTINCT f.fis_id,f.tarih,f.cari,f.aciklama,f.belge_id,"
            "ABS(s.borc_kurus-s.alacak_kurus) tutar FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "WHERE s.hesap_kodu='102' AND f.fis_id NOT IN (SELECT fis_id FROM banka_hareketleri WHERE fis_id IS NOT NULL AND hareket_id<>?) "
            "AND ABS(julianday(f.tarih)-julianday(?))<=7",
            (hareket_id, h["tarih"]),
        ).fetchall()
    adaylar = []
    aciklama = str(h["aciklama"] or "").casefold()
    for r in rows:
        fark = abs(int(r["tutar"]) - abs(int(h["tutar_kurus"])))
        tutar_skor = 60 if fark == 0 else max(0, 60 - int(fark / max(1, abs(h["tutar_kurus"])) * 100))
        gun_farki = abs((_tarih(r["tarih"]) - _tarih(h["tarih"])).days)
        tarih_skor = max(0, 20 - gun_farki * 3)
        metin = f"{r['cari']} {r['aciklama']} {r['belge_id']}".casefold()
        metin_skor = int(20 * SequenceMatcher(None, aciklama, metin).ratio()) if aciklama else 0
        if h["referans"] and str(h["referans"]).casefold() in metin:
            metin_skor = 20
        adaylar.append({"fis_id": r["fis_id"], "tarih": r["tarih"], "cari": r["cari"],
                        "aciklama": r["aciklama"], "tutar": r["tutar"] / 100,
                        "skor": min(100, tutar_skor + tarih_skor + metin_skor)})
    return sorted(adaylar, key=lambda x: x["skor"], reverse=True)[:max(1, int(limit))]


def banka_mutabakat_listesi(durum=None, limit=100):
    veritabanini_hazirla()
    where, args = [], []
    if durum:
        where.append("b.durum=?"); args.append(str(durum).upper())
    sql = (
        "SELECT b.hareket_id,b.hesap,b.tarih,b.aciklama,b.tutar_kurus,b.referans,b.durum,b.fis_id,"
        "f.cari,f.aciklama fis_aciklama FROM banka_hareketleri b LEFT JOIN fisler f ON f.fis_id=b.fis_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY b.tarih DESC,b.hareket_id LIMIT ?"
    )
    args.append(max(1, min(int(limit), 1000)))
    with _baglanti() as con:
        rows = con.execute(sql, args).fetchall()
    return [{**dict(r), "tutar": r["tutar_kurus"] / 100} for r in rows]


def banka_hareketi_eslestir(hareket_id, fis_id, aktor="sistem", zorla=False):
    aday = next((x for x in banka_mutabakat_adaylari(hareket_id, 50) if x["fis_id"] == fis_id), None)
    if not aday:
        raise ValueError("Fiş bu banka hareketi için uygun aday değil.")
    if aday["skor"] < 70 and not zorla:
        raise ValueError("Eşleşme skoru düşük; zorla onayı gerekli.")
    with _baglanti() as con:
        con.execute("UPDATE banka_hareketleri SET durum='ESLESTI',fis_id=? WHERE hareket_id=?", (fis_id, hareket_id))
        con.execute("INSERT INTO banka_eslestirme_gecmisi(hareket_id,fis_id,eylem,skor,aktor,zaman) VALUES(?,?,?,?,?,?)",
                    (hareket_id, fis_id, "ESLESTIR", aday["skor"], str(aktor), _simdi()))
    audit_yaz("banka_eslestirildi", hareket_id=hareket_id, fis_id=fis_id, skor=aday["skor"], aktor=str(aktor))
    return aday


def banka_eslesmesini_kaldir(hareket_id, aktor="sistem"):
    veritabanini_hazirla()
    with _baglanti() as con:
        row = con.execute("SELECT fis_id FROM banka_hareketleri WHERE hareket_id=?", (hareket_id,)).fetchone()
        if not row or not row["fis_id"]:
            raise ValueError("Eşleşmiş banka hareketi bulunamadı.")
        con.execute("UPDATE banka_hareketleri SET durum='ESLESMEDI',fis_id=NULL WHERE hareket_id=?", (hareket_id,))
        con.execute("INSERT INTO banka_eslestirme_gecmisi(hareket_id,fis_id,eylem,aktor,zaman) VALUES(?,?,?,?,?)",
                    (hareket_id, row["fis_id"], "KALDIR", str(aktor), _simdi()))
    audit_yaz("banka_eslesmesi_kaldirildi", hareket_id=hareket_id, fis_id=row["fis_id"], aktor=str(aktor))


def belge_butunlugu_dogrula(belge_id, dosya_yolu=None):
    veritabanini_hazirla()
    with _baglanti() as con:
        row = con.execute("SELECT * FROM belge_butunlugu WHERE belge_id=?", (str(belge_id),)).fetchone()
        if not row:
            raise ValueError("Belge bütünlük manifestinde bulunamadı.")
        yol = Path(dosya_yolu or row["kaynak"]) if (dosya_yolu or row["kaynak"]) else None
        if not yol or not yol.is_file():
            durum, mevcut = "DOSYA_YOK", None
        else:
            mevcut = belge_sha256(yol)
            durum = "DOGRULANDI" if mevcut == row["sha256"] else "DEGISTIRILMIS"
        con.execute("UPDATE belge_butunlugu SET son_dogrulama=?,dogrulama_durumu=? WHERE belge_id=?",
                    (_simdi(), durum, str(belge_id)))
    audit_yaz("belge_butunlugu_kontrol", belge_id=belge_id, durum=durum)
    return {"belge_id": belge_id, "durum": durum, "beklenen_sha256": row["sha256"], "mevcut_sha256": mevcut}


def donem_kapanis_kontrolu(yil, ay):
    """Dönem kapanmadan önce finansal ve operasyonel istisnaları listeler."""
    veritabanini_hazirla()
    bas = date(int(yil), int(ay), 1)
    son = (bas.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    with _baglanti() as con:
        dengesiz = con.execute(
            "SELECT COUNT(*) adet FROM (SELECT f.fis_id FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "WHERE f.tarih BETWEEN ? AND ? GROUP BY f.fis_id HAVING SUM(s.borc_kurus)<>SUM(s.alacak_kurus))",
            (bas.isoformat(), son.isoformat()),
        ).fetchone()["adet"]
        banka = con.execute("SELECT COUNT(*) adet FROM banka_hareketleri WHERE tarih BETWEEN ? AND ? AND durum<>'ESLESTI'",
                            (bas.isoformat(), son.isoformat())).fetchone()["adet"]
        vade = con.execute(
            "SELECT COUNT(DISTINCT f.fis_id) adet FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
            "WHERE f.tarih BETWEEN ? AND ? AND s.hesap_kodu IN ('120','320') AND TRIM(COALESCE(f.vade,''))=''",
            (bas.isoformat(), son.isoformat()),
        ).fetchone()["adet"]
    ic = sirketler_arasi_mutabakat(bas, son)
    ic_fark = sum(1 for x in ic if x["durum"] != "UYUMLU")
    engeller = []
    if dengesiz: engeller.append(f"{dengesiz} dengesiz fiş")
    uyarilar = []
    if banka: uyarilar.append(f"{banka} banka hareketi mutabakatsız")
    if vade: uyarilar.append(f"{vade} açık kalemde vade eksik")
    if ic_fark: engeller.append(f"{ic_fark} şirketler arası bakiye farklı")
    return {"donem": f"{yil:04d}-{ay:02d}", "kapanabilir": not engeller,
            "engeller": engeller, "uyarilar": uyarilar,
            "dengesiz_fis": dengesiz, "mutabakatsiz_banka": banka,
            "vadesiz_acik_kalem": vade, "sirketler_arasi_fark": ic_fark}


def kurumsal_durum(faaliyet=None, rapor_tarihi=None):
    """Dashboard için kısa kurumsal kontrol özeti."""
    alacak = cari_yaslandirma("120", faaliyet, rapor_tarihi)
    borc = cari_yaslandirma("320", faaliyet, rapor_tarihi)
    with _baglanti() as con:
        banka = con.execute("SELECT COUNT(*) adet,COALESCE(SUM(ABS(tutar_kurus)),0) tutar FROM banka_hareketleri WHERE durum<>'ESLESTI'").fetchone()
        belge = con.execute("SELECT dogrulama_durumu,COUNT(*) adet FROM belge_butunlugu GROUP BY dogrulama_durumu").fetchall()
    return {
        "alacak_yaslandirma": {"toplam": sum(x["toplam"] for x in alacak), "gecikmis": sum(x["1_30"] + x["31_60"] + x["61_90"] + x["90_ustu"] for x in alacak), "cariler": alacak[:10]},
        "borc_yaslandirma": {"toplam": sum(x["toplam"] for x in borc), "gecikmis": sum(x["1_30"] + x["31_60"] + x["61_90"] + x["90_ustu"] for x in borc), "cariler": borc[:10]},
        "banka_mutabakat": {"bekleyen": banka["adet"], "tutar": banka["tutar"] / 100},
        "belge_butunlugu": {r["dogrulama_durumu"] or "BILINMIYOR": r["adet"] for r in belge},
    }
