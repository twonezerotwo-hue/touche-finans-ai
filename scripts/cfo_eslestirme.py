# -*- coding: utf-8 -*-
"""CFO akilli eslestirme: bir odeme/tahsilatin hangi ACIK borc/alacagi kapattigini DETERMINISTIK bulur.

SALT-OKUNUR: merkezi defteri yalniz okur, hicbir sey yazmaz. Yapay zeka kullanilmaz.

Kavramlar
  * Acik kalem: bir projede, (etkin) cari bazinda bakiyesi suren borc (BORC) ya da alacak (ALACAK).
    Etkin cari = fisin kapama etiketi (``[Borç kapama: X]``) varsa X, yoksa fisin kendi cari'si.
  * Eski kayitlar: odeme kisiye ("Sebahattin Çakmak") yazilmis, borc ise is adiyla ("Taş işleri") acilmis olabilir.
    Kendi carisinde karsiligi olmayan (avans gorunumlu) odeme, ACIKLAMASINDA acik borcun adi geciyorsa ve
    bu borc tek ise o borca ATFEDILIR. (Yalniz okuma/raporlama; eski kayit degistirilmez.)

Kanit turleri ve puan
  CARI   odemeyi alan, borcun kendi carisi                          -> 100
  METIN + GECMIS  metin/is kalemi borcu adlandiriyor VE kisi bu borca daha once odeme yapmis -> 100
  METIN yalniz | GECMIS yalniz                                      ->  75
Karar: tek aday ve puan >= 100 ve tutar kalani asmiyor -> ESLESTI (otomatik); tek zayif aday -> ONAY (sor);
birden cok aday -> BELIRSIZ (sor); acik kalem var ama kanit yok -> YOK (sor); hic acik kalem yok -> ACIK_YOK.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field

import proje_finansi as pf
from cfo_varliklar import _alias_eslesmesi, jetonlar, norm

BORC, ALACAK = "BORC", "ALACAK"
OTOMATIK_ESIK = 100


@dataclass
class AcikKalem:
    tur: str
    cari: str
    proje: str
    toplam_kurus: int = 0                # tahakkuk (borc) / hakedis (alacak) toplami
    dogrudan_kapanan_kurus: int = 0      # carinin kendi (ve etiketli) odeme/tahsilatlari
    atfedilen_kurus: int = 0             # aciklamadan atfedilen eski odemeler
    kalan_kurus: int = 0
    anahtarlar: tuple = ()
    atamalar: list = field(default_factory=list)   # [{"fis_id","cari","tarih","tutar_kurus","yontem","aciklama"}]


def _proje_fisleri(faaliyet_key, proje):
    try:
        with pf.okuma_baglantisi() as con:
            if con is None:
                return []
            fisler, _ic = pf._satirlari_oku(con, faaliyet_key)
    except sqlite3.OperationalError:
        return []
    p = norm(proje)
    return [f for f in fisler.values() if f["durum"] == "MUHASEBELESTI" and not f["ic"] and norm(f["proje"]) == p]


def _anahtarlar(cari, kalemler):
    """Borcu adlandiran normalize anahtarlar: cari adi + (adi/grubu cariyle ayni olan) is kalemlerinin ad/grubu."""
    k = {norm(cari)}
    for ad, grup in kalemler:
        if norm(ad) == norm(cari) or norm(grup) == norm(cari):
            k.update(x for x in (norm(ad), norm(grup)) if x)
    return tuple(sorted(a for a in k if len(a.replace(" ", "")) >= 4))


def _metinde(anahtar, jt):
    return _alias_eslesmesi(jt, anahtar) is not None


def _tahakkuk_ve_kapanan(fl, tur):
    toplam = kapanan = 0
    hesaplar = pf.BORC if tur == BORC else pf.ALACAK
    for f in fl:
        for kod, _tip, b, a in f["satirlar"]:
            if kod not in hesaplar:
                continue
            net = (a - b) if tur == BORC else (b - a)
            if net > 0:
                toplam += net
            else:
                kapanan -= net
    return toplam, kapanan


def _hesapla(faaliyet_key, proje, kalemler=()):
    """(acik kalemler, gecmis sayaci, tum borclar) — hepsi yalniz okumadan."""
    fl = _proje_fisleri(faaliyet_key, proje)
    gruplar: dict[str, list] = {}
    for f in fl:
        cari = pf.etkin_cari(f)
        if cari:
            gruplar.setdefault(cari, []).append(f)
    kalemler = [(str(a or ""), str(g or "")) for a, g in kalemler]
    tum: list[AcikKalem] = []
    for cari, liste in gruplar.items():
        h = pf.hesapla(liste)
        for tur, bakiye in ((BORC, h["acik_borc"]), (ALACAK, h["kalan_alacak"])):
            toplam, kapanan = _tahakkuk_ve_kapanan(liste, tur)
            if toplam <= 0 and bakiye <= 0:
                continue
            tum.append(AcikKalem(tur, cari, proje, toplam, kapanan, 0, bakiye, _anahtarlar(cari, kalemler)))
    borclar = [k for k in tum if k.tur == BORC]
    # ---- odeme atfi: etiket > aciklama (yalniz kendi carisinde karsiligi olmayan odemeler) ----
    kendi_bakiye = {c: pf.hesapla(l)["acik_borc"] for c, l in gruplar.items()}
    gecmis: Counter = Counter()
    for f in fl:
        if pf.fis_sinifi(f) != "Ödeme":
            continue
        odenen = pf._katki(f["satirlar"])["odenen"]
        etiket = pf.baglanti_coz(f.get("aciklama"))
        dogrudan = next((k for k in borclar if norm(k.cari) == norm(f["cari"])), None)
        hedef = yontem = None
        if etiket and etiket[0] == BORC:
            hedef = next((k for k in borclar if norm(k.cari) == norm(etiket[1])), None)
            yontem = "etiket"
        elif dogrudan is not None:
            hedef, yontem = dogrudan, "dogrudan"
        elif kendi_bakiye.get(f["cari"], 0) <= 0:
            # kendi carisinde karsiligi olmayan (avans gorunumlu) odeme: aciklamada TEK acik borcun adi geciyorsa atfedilir
            jt = jetonlar(f.get("aciklama"))
            adaylar = [k for k in borclar if k.anahtarlar and any(_metinde(a, jt) for a in k.anahtarlar)]
            if len(adaylar) == 1:
                hedef, yontem = adaylar[0], "aciklama"
        if hedef is None:
            continue
        hedef.atamalar.append({"fis_id": f["fis_id"], "cari": f["cari"], "tarih": f["tarih"], "tutar_kurus": odenen,
                               "yontem": yontem, "aciklama": f.get("aciklama", "")[:120]})
        if yontem == "aciklama":
            hedef.atfedilen_kurus += odenen
            hedef.kalan_kurus -= odenen
        if norm(f["cari"]) != norm(hedef.cari):
            gecmis[(norm(f["cari"]), norm(hedef.cari))] += 1
    acik = [k for k in tum if k.kalan_kurus > 0]
    return acik, gecmis, tum


def _ozet(k: AcikKalem) -> dict:
    return {"tur": k.tur, "cari": k.cari, "proje": k.proje, "toplam_kurus": k.toplam_kurus,
            "kalan_kurus": k.kalan_kurus, "atfedilen_kurus": k.atfedilen_kurus}


def eslestir(faaliyet_key, proje, cari, tutar_kurus, tur, metin="", kalem=None, kalemler=()) -> dict:
    """Odeme (``BORC``) veya tahsilat/alacak silme (``ALACAK``) icin acik kalem adaylarini bulur.

    ``kalem``: secilmis is kalemi (ad, grup) ya da None; ``kalemler``: projedeki tum (ad, grup) cifti.
    Donus: {"karar": ESLESTI|ONAY|BELIRSIZ|YOK|ACIK_YOK, "adaylar": [...], "acik_kalemler": [...]}
    """
    acik, gecmis, _tum = _hesapla(faaliyet_key, proje, kalemler)
    uygun = [k for k in acik if k.tur == tur]
    jt = jetonlar(metin)
    kalem_adlari = {norm(x) for x in (kalem or ()) if x}
    adaylar = []
    for k in uygun:
        kanit, cari_k = [], norm(cari) == norm(k.cari)
        metin_k = any(_metinde(a, jt) for a in k.anahtarlar) or any(a in kalem_adlari for a in k.anahtarlar)
        n_gecmis = gecmis.get((norm(cari), norm(k.cari)), 0) if not cari_k else 0
        if cari_k:
            kanit.append("ödeme doğrudan bu carinin borcuna yapılıyor" if tur == BORC
                         else "tahsilat doğrudan bu carinin alacağına yapılıyor")
        if metin_k:
            kanit.append("metinde/iş kaleminde bu borç adı geçiyor" if tur == BORC else "metinde bu alacak adı geçiyor")
        if n_gecmis:
            kanit.append(f"{cari} daha önce bu borca {n_gecmis} ödeme yapmış")
        skor = 100 if (cari_k or (metin_k and n_gecmis)) else (75 if (metin_k or n_gecmis) else 0)
        if not skor:
            continue
        adaylar.append({**_ozet(k), "skor": skor, "kanit": kanit, "asiyor": tutar_kurus > k.kalan_kurus,
                        "kalan_sonra_kurus": max(k.kalan_kurus - tutar_kurus, 0)})
    adaylar.sort(key=lambda a: (-a["skor"], -a["kalan_kurus"], a["cari"]))
    if not uygun:
        karar = "ACIK_YOK"
    elif not adaylar:
        karar = "YOK"
    elif len(adaylar) == 1:
        a = adaylar[0]
        karar = "ESLESTI" if a["skor"] >= OTOMATIK_ESIK and not a["asiyor"] else "ONAY"
    else:
        karar = "BELIRSIZ"
    return {"karar": karar, "adaylar": adaylar,
            "acik_kalemler": sorted((_ozet(k) for k in uygun), key=lambda x: -x["kalan_kurus"])}


# ---------------------------------------------------------------------------
# Mevcut veri raporu (SALT-OKUNUR; hicbir kayit degistirilmez)
# ---------------------------------------------------------------------------

def rapor(faaliyet_key, proje, kalemler=()) -> dict:
    acik, _gecmis, tum = _hesapla(faaliyet_key, proje, kalemler)
    borclar = []
    for k in sorted((x for x in tum if x.tur == BORC and x.toplam_kurus > 0), key=lambda x: -x.toplam_kurus):
        borclar.append({
            "cari": k.cari, "toplam_kurus": k.toplam_kurus, "dogrudan_kapanan_kurus": k.dogrudan_kapanan_kurus,
            "atfedilen_kurus": k.atfedilen_kurus, "kalan_kurus": k.kalan_kurus,
            "odemeler": [a for a in k.atamalar],
        })
    yanlis_cari = [
        {**a, "hedef_borc": b["cari"]}
        for b in borclar for a in b["odemeler"] if norm(a["cari"]) != norm(b["cari"]) and a["yontem"] == "aciklama"
    ]
    fl = _proje_fisleri(faaliyet_key, proje)
    gruplar: dict[str, list] = {}
    for f in fl:
        gruplar.setdefault(f["cari"], []).append(f)
    eksi = [{"cari": c, "acik_borc_kurus": h["acik_borc"]} for c, l in gruplar.items()
            if (h := pf.hesapla(l))["acik_borc"] < 0]
    return {"faaliyet": faaliyet_key, "proje": proje, "borclar": borclar, "yanlis_cari_odemeleri": yanlis_cari,
            "eksi_bakiyeli_cariler": sorted(eksi, key=lambda x: x["acik_borc_kurus"]),
            "acik_alacaklar": [_ozet(k) for k in acik if k.tur == ALACAK]}


def _para(kurus) -> str:
    return pf.tl_metin(kurus / 100, kusuratsiz=False)


def rapor_metni(r: dict) -> str:
    s = [f"MEVCUT VERİ RAPORU (salt-okunur) — {r['faaliyet']} / {r['proje']}", ""]
    for b in r["borclar"]:
        s.append(f"{b['cari']}: borç {_para(b['toplam_kurus'])} · kendi carisinde kapanan {_para(b['dogrudan_kapanan_kurus'])}"
                 f" · açıklamadan atfedilen ödeme {_para(b['atfedilen_kurus'])} · KALAN {_para(b['kalan_kurus'])}")
        for a in b["odemeler"]:
            ek = "" if norm(a["cari"]) == norm(b["cari"]) else f" (cari: {a['cari']})"
            s.append(f"    {a['tarih']} {_para(a['tutar_kurus'])} [{a['yontem']}]{ek} {a['fis_id']}")
    s += ["", "Yanlış cari altında duran (aynı açık borca bağlanması gereken) ödemeler:"]
    for a in r["yanlis_cari_odemeleri"]:
        s.append(f"  {a['fis_id']}: {_para(a['tutar_kurus'])} · şu an cari '{a['cari']}' → '{a['hedef_borc']}' borcuna bağlanmalı")
    if not r["yanlis_cari_odemeleri"]:
        s.append("  (yok)")
    s += ["", "Eksi bakiyeli (ödemesi kendi carisinde karşılıksız kalan) cariler:"]
    for e in r["eksi_bakiyeli_cariler"]:
        s.append(f"  {e['cari']}: {_para(e['acik_borc_kurus'])}")
    if r["acik_alacaklar"]:
        s += ["", "Açık alacaklar:"] + [f"  {a['cari']}: kalan {_para(a['kalan_kurus'])}" for a in r["acik_alacaklar"]]
    return "\n".join(s)


if __name__ == "__main__":                                   # pragma: no cover
    import argparse

    from cfo_varliklar import katalog_yukle

    ap = argparse.ArgumentParser(description="Projedeki acik borc/alacak ve eski odeme eslesme raporu (salt-okunur)")
    ap.add_argument("--faaliyet", required=True)
    ap.add_argument("--proje", required=True)
    a = ap.parse_args()
    kat = katalog_yukle()
    kalemler = [(k.ad, k.ekle("grup")) for k in kat.kalemleri(a.faaliyet, a.proje)]
    print(rapor_metni(rapor(a.faaliyet, a.proje, kalemler)))
