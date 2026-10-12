# -*- coding: utf-8 -*-
"""Holding / faaliyet / proje icin DETERMINISTIK finansal metrikler (salt-okunur).

Tum tutarlar merkezi defterden (fisler + fis_satirlari) hesaplanir; LLM sayi uretmez.
Kavramlar BIRBIRINE KARISTIRILMAZ:

  Gelir (kazanilan)   : GELIR hesaplari (600/646/649) net alacak     != Tahsilat
  Gider (maliyet)     : GIDER hesaplari (740/770/780/799) net borc   != Odeme
  Tahsil edilen       : alacak hesabini (120/131/159) kapatan nakit girisi
  Odenen              : nakit hesaplarindan (100/101/102) cikan tutar
  Kalan alacak        : 120/131/159 net borc bakiyesi
  Acik borc           : 300/320/331/335/336 net alacak bakiyesi
  Kar / zarar         : Gelir - Gider              (tahakkuk / sonuc)
  Net nakit           : Tahsil edilen + dogrudan nakit gelir - Odenen   (nakit akisi)

Hesaplanamayan metrik uydurulmaz: ``DATA_NOT_AVAILABLE`` doner. Grup ici (intercompany)
fisler gider/odeme/tahsilat sayilmaz; holding toplamlarinda 131/331 satirlari elimine edilir.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import finans_cekirdegi as cekirdek
from cfo_varliklar import Katalog, katalog_yukle, norm
from money import tl_metin, yuzde_metin

NAKIT = ("100", "101", "102")
ALACAK = ("120", "131", "159")
BORC = ("300", "320", "331", "335", "336")
GELIR = ("600", "646", "649")
GIDER = ("740", "770", "780", "799")
IC_HESAPLAR = ("131", "331")
DNA = "DATA_NOT_AVAILABLE"


@contextmanager
def okuma_baglantisi():
    """Yalniz-okunur baglanti: ``query_only`` ile yazma motor duzeyinde engellenir."""
    yol = Path(cekirdek.DB_PATH)
    if not yol.exists():
        yield None
        return
    con = sqlite3.connect(yol, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


def _m(kurus, kaynak="", aciklama="", var=True):
    if not var or kurus is None:
        return {"durum": DNA, "deger_kurus": None, "metin": DNA, "kaynak": kaynak, "aciklama": aciklama}
    return {"durum": "OK", "deger_kurus": int(kurus), "metin": tl_metin(int(kurus) / 100, kusuratsiz=False),
            "kaynak": kaynak, "aciklama": aciklama}


def _satirlari_oku(con, faaliyet_key=None):
    """Kapsamdaki tum defter satirlari (fis basina gruplanmis)."""
    if con is None:
        return {}, set()
    ic = set()
    try:
        for r in con.execute("SELECT kaynak_fis_id a, hedef_fis_id b FROM sirketler_arasi_islemler"):
            ic.update((r["a"], r["b"]))
    except sqlite3.OperationalError:
        pass
    sorgu = ("SELECT f.fis_id, f.belge_id, f.faaliyet_key, f.proje, f.cari, f.tarih, f.durum, f.aciklama, "
             "f.vade, f.is_kalemi_id, s.hesap_kodu, s.borc_kurus b, s.alacak_kurus a, h.hesap_tipi tip "
             "FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id JOIN hesaplar h ON h.kod=s.hesap_kodu")
    args = []
    if faaliyet_key:
        sorgu += " WHERE f.faaliyet_key=?"
        args.append(faaliyet_key)
    sorgu += " ORDER BY f.tarih, f.fis_id, s.satir_no"
    try:
        rows = con.execute(sorgu, args).fetchall()
    except sqlite3.OperationalError:
        # kalem kolonu (ek sema) olmayan eski DB
        sorgu = sorgu.replace("f.is_kalemi_id, ", "NULL is_kalemi_id, ")
        rows = con.execute(sorgu, args).fetchall()
    fisler: dict = {}
    for r in rows:
        f = fisler.setdefault(r["fis_id"], {
            "fis_id": r["fis_id"], "belge_id": r["belge_id"], "faaliyet": r["faaliyet_key"], "proje": r["proje"] or "",
            "cari": r["cari"] or "", "tarih": r["tarih"], "durum": r["durum"], "aciklama": r["aciklama"] or "",
            "vade": r["vade"], "kalem": r["is_kalemi_id"] or "", "ic": r["fis_id"] in ic, "satirlar": []})
        f["satirlar"].append((r["hesap_kodu"], r["tip"], r["b"], r["a"]))
    return fisler, ic


def _toplam(f, hesaplar, taraf):
    return sum(s[2] if taraf == "b" else s[3] for s in f["satirlar"] if s[0] in hesaplar)


def fis_sinifi(f) -> str:
    cb, ca = _toplam(f, NAKIT, "b"), _toplam(f, NAKIT, "a")
    if f["ic"]:
        return "Grup içi transfer"
    if ca and _toplam(f, BORC, "b"):
        return "Ödeme"
    if cb and _toplam(f, ALACAK, "a"):
        return "Tahsilat"
    if _toplam(f, ALACAK, "b") and sum(s[3] for s in f["satirlar"] if s[1] == "GELIR"):
        return "Hakediş / gelir tahakkuku"
    if sum(s[2] for s in f["satirlar"] if s[1] == "GIDER") and _toplam(f, BORC, "a"):
        return "Gider tahakkuku"
    if cb and sum(s[3] for s in f["satirlar"] if s[1] == "GELIR"):
        return "Nakit gelir"
    if cb and _toplam(f, ("300", "336"), "a"):
        return "Finansman girişi"
    if ca and sum(s[2] for s in f["satirlar"] if s[1] == "GIDER"):
        return "Nakit gider"
    return "Diğer"


TICARI_BORC = ("320", "335")          # tedarikci/personel: iade bunlari azaltir
FINANS_BORC = ("300", "331", "336")   # kredi/ortak/diger finansal borclar: giris = finansman


def _katki(satirlar) -> dict:
    """Tek fisin metrik katkisi. Nakit hareketi, KARSI HESABINA gore siniflanir (simetrik)."""
    def top(hesaplar, i):
        return sum(x[i] for x in satirlar if x[0] in hesaplar)

    gelir = sum(x[3] - x[2] for x in satirlar if x[1] == "GELIR")
    gider = sum(x[2] - x[3] for x in satirlar if x[1] == "GIDER")
    cb, ca = top(NAKIT, 2), top(NAKIT, 3)
    gelir_cr = sum(x[3] for x in satirlar if x[1] == "GELIR")
    # nakit girisi: alacak kapatma (tahsilat) > dogrudan gelir > tedarikci iadesi > finansman
    tahsilat = min(cb, top(ALACAK, 3))
    kalan = cb - tahsilat
    nakit_gelir = min(kalan, gelir_cr)
    kalan -= nakit_gelir
    iade = min(kalan, top(TICARI_BORC, 3))
    finansman = kalan - iade
    # nakit cikisi: alacak hesabi borclanmissa tahsilat iptali; kalani odeme
    tahsilat_iptali = min(ca, top(ALACAK, 2))
    odeme_borc = min(ca - tahsilat_iptali, top(TICARI_BORC + FINANS_BORC, 2))
    return {"gelir": gelir, "gider": gider, "tahsilat": tahsilat - tahsilat_iptali, "nakit_gelir": nakit_gelir,
            "odenen": ca - tahsilat_iptali - iade, "finansman_girisi": finansman, "borc_odemesi": odeme_borc,
            "odeme": 1 if ca - tahsilat_iptali > 0 else 0, "tahsilat_adedi": 1 if tahsilat > 0 else 0}


def hesapla(fisler, *, ic_dahil=False) -> dict:
    """Fis listesinden ana metrikleri (kurus) hesaplar.

    Grup ici (intercompany) fislerin 131/331 satirlari bakiyelerden cikarilir ve nakit
    hareketleri gider/odeme/tahsilat SAYILMAZ (``grup_ici_net`` ayrica raporlanir).
    """
    toplam = dict.fromkeys(("gelir", "gider", "tahsilat", "nakit_gelir", "odenen", "finansman_girisi",
                            "borc_odemesi"), 0)
    alacak = borc = ic_net = odeme_adedi = tahsilat_adedi = 0
    for f in fisler:
        for kod, tip, b, a in f["satirlar"]:
            if f["ic"] and kod in IC_HESAPLAR and not ic_dahil:
                ic_net += b - a
                continue
            if kod in ALACAK:
                alacak += b - a
            elif kod in BORC:
                borc += a - b
        if f["ic"]:
            continue
        k = _katki(f["satirlar"])
        for ad in toplam:
            toplam[ad] += k[ad]
        if f["durum"] == "MUHASEBELESTI":
            odeme_adedi += k["odeme"]
            tahsilat_adedi += k["tahsilat_adedi"]
    return {**toplam, "kalan_alacak": alacak, "acik_borc": borc, "odeme_adedi": odeme_adedi,
            "tahsilat_adedi": tahsilat_adedi, "grup_ici_net": ic_net, "kar": toplam["gelir"] - toplam["gider"],
            "net_nakit": toplam["tahsilat"] + toplam["nakit_gelir"] - toplam["odenen"]}


def _marj(ham):
    if ham["gelir"] <= 0:
        return None
    return round(ham["kar"] / ham["gelir"] * 100, 2)


def _metrik_sozlugu(ham, kalem_verisi_var=True) -> dict:
    marj = _marj(ham)
    return {
        "kazanilan_gelir": _m(ham["gelir"], "merkezi defter (600/646/649)", "Hakediş/gelir tahakkuku; tahsilat DEĞİLDİR"),
        "tahsil_edilen": _m(ham["tahsilat"], "merkezi defter (alacağı kapatan nakit girişi)"),
        "dogrudan_nakit_gelir": _m(ham["nakit_gelir"], "merkezi defter (nakit gelir)"),
        "odenen": _m(ham["odenen"], "merkezi defter (kasa/banka çıkışı)", "Ödeme gider DEĞİLDİR"),
        "gider": _m(ham["gider"], "merkezi defter (740/770/780/799)", "Gider tahakkuku; ödeme DEĞİLDİR"),
        "kalan_alacak": _m(ham["kalan_alacak"], "merkezi defter (120/131/159 bakiyesi)",
                           "Kazanılan geliri tahsil edilen düşülerek bulunur"),
        "acik_borc": _m(ham["acik_borc"], "merkezi defter (300/320/331/335/336 bakiyesi)",
                        "Negatifse tahakkuku olmayan ödeme/avans vardır"),
        "kar_zarar": _m(ham["kar"], "Gelir − Gider", "Nakit akışı DEĞİL, sonuçtur"),
        "kar_marji": ({"durum": "OK", "deger": marj, "metin": yuzde_metin(marj)} if marj is not None
                      else {"durum": DNA, "deger": None, "metin": DNA, "aciklama": "Gelir sıfır olduğu için marj tanımsız"}),
        "net_nakit": _m(ham["net_nakit"], "Tahsil edilen + nakit gelir − Ödenen", "Kâr değildir"),
    }


def _uyarilar(ham) -> list[str]:
    u = []
    if ham["acik_borc"] < 0:
        u.append(f"Tahakkuku olmayan ödeme/avans var: {tl_metin(-ham['acik_borc'] / 100, kusuratsiz=False)}. "
                 "Gider tahakkuku girilmedikçe maliyet ve kâr eksik/yanlış görünür.")
    if ham["kalan_alacak"] < 0:
        u.append("Kazanç kaydı olmayan tahsilat/avans var; hakediş (gelir tahakkuku) girilmedi.")
    if ham["odenen"] and not ham["gider"] and not ham["acik_borc"]:
        u.append("Ödeme var ama gider kaydı yok; kâr hesabına maliyet yansımıyor.")
    return u


def _kalem_tablosu(kalemler, fisler_kalem, ham_kalem):
    sonuc = []
    for k in kalemler:
        fl = fisler_kalem.get(k["kalem_id"], [])
        h = hesapla(fl)
        plan = int(round(k["maliyet"] * 100))
        odenen = h["odenen"]
        gerc = h["gider"]
        harcanan = max(gerc, odenen)
        if plan > 0 and odenen >= plan * 0.9999:
            ilerleme = "Ödeme tamamlandı"
        elif odenen > 0 or gerc > 0:
            ilerleme = "Devam ediyor"
        else:
            ilerleme = "Başlamadı"
        sonuc.append({
            "kalem_id": k["kalem_id"], "ad": k["is_kalemi"], "grup": k["maliyet_grubu"], "birim": k["birim"],
            "onay_durumu": k["durum"], "ilerleme": ilerleme,
            "gelir_degeri_kurus": int(round(k["ciro"] * 100)), "maliyet_plani_kurus": plan,
            "gerceklesen_gider_kurus": gerc, "odenen_kurus": odenen,
            "kalan_odeme_kurus": gerc - odenen, "butce_kalan_kurus": plan - harcanan,
            "bagli_kayit_sayisi": len(fl),
        })
    return sonuc


def _cari_bakiyeleri(fisler):
    grup = {}
    for f in fisler:
        if f["ic"] or not f["cari"]:
            continue
        g = grup.setdefault(f["cari"], [])
        g.append(f)
    sonuc = []
    for cari, fl in grup.items():
        h = hesapla(fl)
        sonuc.append({"cari": cari, "acik_borc_kurus": h["acik_borc"], "kalan_alacak_kurus": h["kalan_alacak"],
                      "odenen_kurus": h["odenen"], "tahsil_edilen_kurus": h["tahsilat"],
                      "gider_kurus": h["gider"], "gelir_kurus": h["gelir"]})
    return sorted(sonuc, key=lambda x: -(abs(x["odenen_kurus"]) + abs(x["tahsil_edilen_kurus"]) + abs(x["acik_borc_kurus"])))


def _son_islemler(fisler, limit=15):
    uygun = [f for f in fisler if f["durum"] == "MUHASEBELESTI"]
    uygun.sort(key=lambda f: (f["tarih"], f["fis_id"]), reverse=True)
    sonuc = []
    for f in uygun[:limit]:
        tutar = max((max(s[2], s[3]) for s in f["satirlar"]), default=0)
        sonuc.append({"tarih": f["tarih"], "fis_id": f["fis_id"], "belge_id": f["belge_id"], "cari": f["cari"],
                      "proje": f["proje"], "tur": fis_sinifi(f), "tutar_kurus": tutar,
                      "tutar_metin": tl_metin(tutar / 100, kusuratsiz=False), "aciklama": f["aciklama"][:160]})
    return sonuc


def _proje_filtre(fisler, proje):
    p = norm(proje)
    return [f for f in fisler.values() if norm(f["proje"]) == p]


def proje_listesi(faaliyet_key=None, katalog: Katalog | None = None) -> dict:
    """Faaliyet(ler)deki projelerin ozet satirlari (tiklanabilir liste icin)."""
    katalog = katalog or katalog_yukle()
    with okuma_baglantisi() as con:
        fisler, _ = _satirlari_oku(con, None)
    satirlar = []
    for p in katalog.projeleri(faaliyet_key):
        fl = [f for f in fisler.values() if f["faaliyet"] == p.ust and norm(f["proje"]) == norm(p.ad)]
        ham = hesapla(fl)
        f = katalog.faaliyet(p.ust)
        satirlar.append({
            "proje": p.ad, "faaliyet_key": p.ust, "faaliyet": f.gorunen() if f else p.ust,
            "kazanilan_gelir": _m(ham["gelir"]), "gider": _m(ham["gider"]), "kar_zarar": _m(ham["kar"]),
            "tahsil_edilen": _m(ham["tahsilat"]), "odenen": _m(ham["odenen"]),
            "kalan_alacak": _m(ham["kalan_alacak"]), "acik_borc": _m(ham["acik_borc"]),
            "kayit_sayisi": len([x for x in fl if x["durum"] == "MUHASEBELESTI"]),
        })
    return {"projeler": satirlar, "kaynak": "merkezi defter", "veri_tarihi": datetime.now().isoformat(timespec="seconds")}


def proje_detay(faaliyet_key, proje, katalog: Katalog | None = None) -> dict:
    katalog = katalog or katalog_yukle()
    f = katalog.faaliyet(faaliyet_key)
    p = next((x for x in katalog.projeleri(faaliyet_key) if norm(x.ad) == norm(proje)), None)
    if not f or not p:
        raise ValueError("Proje bu faaliyette bulunamadı.")
    with okuma_baglantisi() as con:
        fisler, _ = _satirlari_oku(con, faaliyet_key)
        sozlesme_kurus = _sozlesme_degeri(con, faaliyet_key, p.ad)
    fl = _proje_filtre(fisler, p.ad)
    ham = hesapla(fl)
    # --- is kalemleri (Excel) ---
    kalem_verisi = []
    kalem_ozeti = None
    try:
        import proje_is_kalemleri

        liste = proje_is_kalemleri.listele(faaliyet_key)
        kalem_verisi = [k for k in liste["kalemler"] if norm(k["proje"]) == norm(p.ad) and norm(k["durum"]) != "iptal"]
        kalem_ozeti = proje_is_kalemleri._ozet(kalem_verisi)
    except Exception:
        kalem_verisi = []
    kalem_fisler: dict = {}
    for x in fl:
        if x["kalem"]:
            kalem_fisler.setdefault(x["kalem"], []).append(x)
    kalemler = _kalem_tablosu(kalem_verisi, kalem_fisler, None)
    onayli = [k for k in kalem_verisi if norm(k["durum"]) == "onayli"]
    sozlesme = None
    if sozlesme_kurus:
        sozlesme = _m(sozlesme_kurus, "sözleşme kaydı")
    elif onayli:
        sozlesme = _m(int(round(sum(k["ciro"] for k in onayli) * 100)), "onaylı iş kalemleri geliri (06_Proje İş Kalemleri)")
    else:
        sozlesme = _m(None, "", "Sözleşme kaydı ve onaylı iş kalemi bulunamadı", var=False)
    metrikler = _metrik_sozlugu(ham)
    metrikler["sozlesme_gelir_degeri"] = sozlesme
    tamam = [k for k in kalemler if k["ilerleme"] == "Ödeme tamamlandı"]
    acik = [k for k in kalemler if k["ilerleme"] != "Ödeme tamamlandı" and norm(k["onay_durumu"]) == "onayli"]
    return {
        "proje": p.ad, "faaliyet_key": faaliyet_key, "faaliyet": f.gorunen(),
        "metrikler": metrikler, "uyarilar": _uyarilar(ham),
        "is_kalemleri": kalemler,
        "is_kalemi_ozeti": {
            "toplam": len(kalemler), "acik": len(acik), "odemesi_tamamlanan": len(tamam),
            "tanim": "Açık = onaylı ve planlanan maliyeti henüz ödenmemiş; tamamlanan = ödemesi plana ulaşmış",
            "onayli_gelir_kurus": int(round(kalem_ozeti["ciro"] * 100)) if kalem_ozeti else None,
            "planlanan_maliyet_kurus": int(round(kalem_ozeti["maliyet"] * 100)) if kalem_ozeti else None,
        },
        "cari_bakiyeleri": _cari_bakiyeleri(fl), "son_islemler": _son_islemler(fl),
        "kaynak": "merkezi defter + 06_Proje İş Kalemleri", "veri_tarihi": datetime.now().isoformat(timespec="seconds"),
        "tanimlar": {
            "gelir_tahsilat": "Kazanılan gelir (hakediş) ile tahsil edilen para farklıdır.",
            "gider_odeme": "Gider (tahakkuk) ile ödenen para farklıdır.",
            "kar_nakit": "Kâr = Gelir − Gider; net nakit = tahsilat − ödeme.",
        },
    }


def _sozlesme_degeri(con, faaliyet_key, proje):
    if con is None:
        return None
    try:
        satirlar = con.execute(
            "SELECT proje, tutar_kurus, para_birimi FROM sozlesmeler WHERE faaliyet_key=? AND durum='AKTIF'",
            (faaliyet_key,)).fetchall()
    except sqlite3.OperationalError:
        return None
    toplam = sum(r["tutar_kurus"] for r in satirlar if norm(r["proje"]) == norm(proje)
                 and str(r["para_birimi"] or "TL").upper() in ("TL", "TRY"))
    return toplam or None


def faaliyet_detay(faaliyet_key, katalog: Katalog | None = None) -> dict:
    katalog = katalog or katalog_yukle()
    f = katalog.faaliyet(faaliyet_key)
    if not f:
        raise ValueError("Faaliyet bulunamadı.")
    with okuma_baglantisi() as con:
        fisler, _ = _satirlari_oku(con, faaliyet_key)
    fl = list(fisler.values())
    ham = hesapla(fl)
    projeler = proje_listesi(faaliyet_key, katalog)["projeler"]
    return {
        "faaliyet_key": faaliyet_key, "faaliyet": f.gorunen(), "excel_adi": f.ad,
        "metrikler": _metrik_sozlugu(ham), "uyarilar": _uyarilar(ham),
        "grup_ici_net": _m(ham["grup_ici_net"], "merkezi defter (131/331 grup içi)",
                           "Gider/ödeme/tahsilat sayılmaz; holding toplamında elimine edilir"),
        "projeler": projeler, "cari_bakiyeleri": _cari_bakiyeleri(fl)[:20], "son_islemler": _son_islemler(fl),
        "projesiz_odenen": _m(hesapla([x for x in fl if not x["proje"]])["odenen"]),
        "kaynak": "merkezi defter", "veri_tarihi": datetime.now().isoformat(timespec="seconds"),
    }


def holding_ozeti(katalog: Katalog | None = None) -> dict:
    """Faaliyetleri GERCEK defter kayitlarindan toplar; grup ici bakiyeler elimine edilir."""
    katalog = katalog or katalog_yukle()
    with okuma_baglantisi() as con:
        fisler, ic = _satirlari_oku(con, None)
    tum = list(fisler.values())
    satirlar = []
    for f in katalog.faaliyetler:
        fl = [x for x in tum if x["faaliyet"] == f.kimlik]
        ham = hesapla(fl)
        satirlar.append({"faaliyet_key": f.kimlik, "faaliyet": f.gorunen(), "metrikler": _metrik_sozlugu(ham),
                         "kayit_sayisi": len([x for x in fl if x["durum"] == "MUHASEBELESTI"])})
    # Defterde olup katalogda tanimli olmayan faaliyetler (eski/hayalet) acikca gosterilir
    bilinen = {f.kimlik for f in katalog.faaliyetler}
    kayitsiz = sorted({x["faaliyet"] for x in tum} - bilinen)
    ham = hesapla(tum)
    kons = _metrik_sozlugu(ham)
    return {
        "konsolide": kons, "faaliyetler": satirlar, "uyarilar": _uyarilar(ham) +
        ([f"Defterde Excel tanımı olmayan faaliyet(ler): {', '.join(kayitsiz)}"] if kayitsiz else []),
        "grup_ici_elimine_edilen_net": _m(0, "131/331 satırları konsolidasyonda elimine edilir"),
        "son_islemler": _son_islemler(tum, 10),
        "kaynak": "merkezi defter", "veri_tarihi": datetime.now().isoformat(timespec="seconds"),
    }
