# -*- coding: utf-8 -*-
"""CFO okuma sorgulari: dogal dil -> yapisal sorgu -> deterministik hesap -> yapisal sonuc -> aciklama.

* Salt-okunur: defter baglantisi ``query_only``; Excel hic acilmaz/yazilmaz.
* Sayilari LLM uretmez; aciklama sablondur ve her cevapta KAPSAM, DONEM, KAYNAK ve veri tarihi yazar.
* Varlik filtresi (faaliyet/proje/cari) belirsizse tahmin edilmez, netlestirme istenir.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import cfo_dil
import cfo_niyet as N
import proje_finansi as pf
from cfo_varliklar import Katalog, Varlik, katalog_yukle, norm
from guvenlik import audit_yaz
from money import tl_metin

# metrik -> (hesapla anahtari, baslik, "yapildi" fiili)
METRIKLER = {
    "ODENEN": ("odenen", "ödeme", "Toplam ödeme"),
    "TAHSIL": ("tahsilat", "tahsilat", "Toplam tahsilat"),
    "KAZANC": ("gelir", "kazanılan gelir (hakediş/gelir tahakkuku)", "Kazanılan gelir"),
    "KALAN_ALACAK": ("kalan_alacak", "kalan alacak", "Kalan alacak"),
    "ACIK_BORC": ("acik_borc", "açık borç", "Açık borç"),
    "GIDER": ("gider", "gider (maliyet tahakkuku)", "Toplam gider"),
    "KAR": ("kar", "kâr/zarar", "Kâr/zarar"),
    "NET_NAKIT": ("net_nakit", "net nakit (tahsilat − ödeme)", "Net nakit"),
}


@dataclass
class Sorgu:
    metrik: str | None = None
    faaliyet: Varlik | None = None
    proje: Varlik | None = None
    cari: Varlik | None = None
    bas: date | None = None
    bit: date | None = None
    donem_etiketi: str = "Tüm dönem"
    belirsiz: dict = field(default_factory=dict)
    detay: bool = False
    kayit_kimligi: str = ""
    notlar: list = field(default_factory=list)       # kapsam/anlama varsayimlari: cevapta aynen gosterilir


_KIMLIK_RE = re.compile(r"\b((?:ai-th|ai-gur)-[a-z0-9]+|[a-z]{4}-chat-[a-z0-9]+)\b")


def metrik_cikar(sade: str, ham: str = ""):
    if _KIMLIK_RE.search(ham.lower()) and re.search(r"neden|nereden|kim|ne zaman|hangi|gecmis", sade):
        return "KAYIT_GECMISI"
    if re.search(r"\bkazan\w*|\bkazanc\b", sade):
        return "KAZANC"
    # "Isveren bize ne kadar borclu": KARSI TARAFIN borcu = bizim alacagimiz. "borcumuz/borcluyuz" = bizim borcumuz.
    if re.search(r"\bbize\s+(?:\w+\s+){0,3}borc\w*", sade) or (
            re.search(r"\bborclu\b", sade) and not re.search(r"\bborclu\s+(?:muyuz|miyiz)\b|\bborcluyuz\b", sade)):
        return "KALAN_ALACAK"
    if re.search(r"\balacakli\b", sade):             # "Devrim ne kadar alacakli": O alacakli, yani biz borcluyuz
        return "ACIK_BORC"
    # Buraya yalniz SORGU cumleleri gelir ("alacaklarimiz ne durumda", "borcumuz var mi"): ek kosul gerekmez.
    if re.search(r"\balacag\w*|\balacak\w*", sade):
        return "KALAN_ALACAK"
    if re.search(r"\bborc\w*", sade):
        return "ACIK_BORC"
    if re.search(r"\btahsil\w*", sade):
        return "TAHSIL"
    if re.search(r"\bkari?\b|\bkarli\w*|\bzarar\w*", sade):
        return "KAR"
    if re.search(r"\bnet nakit\b|\bnakit sonuc\w*", sade):
        return "NET_NAKIT"
    if re.search(r"\bgelir\w*|\bciro\w*", sade):      # "gelirimiz", "ciro"; "gelir gider farki" = kar/zarar
        return "KAR" if re.search(r"\bgider\w*|\bmasraf\w*", sade) else "KAZANC"
    if re.search(r"\bgider\w*|\bmasraf\w*|\bmaliyet\w*", sade) and not re.search(r"\bodeme\w*|\bodedi\w*|\bodend\w*", sade):
        return "GIDER"
    if re.search(r"\bodeme\w*|\bodedi\w*|\boden\w*|\bodeyec\w*|\bodeyis\w*|\bode\b|\bharca\w*", sade) or "yaptik" in sade:
        return "ODENEN"
    return None


def sorgu_coz(metin, katalog: Katalog, bugun: date | None = None) -> Sorgu | None:
    """Yapisal sorgu cikarir; desteklenen bir metrik yoksa None (mevcut danisman hattina birakilir)."""
    bugun = bugun or date.today()
    sade = norm(metin)
    metrik = metrik_cikar(sade, str(metin))
    if not metrik:
        return None
    s = Sorgu(metrik=metrik)
    if metrik == "KAYIT_GECMISI":
        m = _KIMLIK_RE.search(str(metin).lower())
        s.kayit_kimligi = m.group(1).upper() if m else ""
        return s
    # varliklar (kayit cumlesiyle ayni cozumleme; zorla=True: sorguda da varlik cikar)
    n = N.analiz_et(metin, katalog, bugun, zorla=True)
    s.faaliyet, s.proje, s.cari = n.faaliyet, n.proje, n.cari
    # Sorgu SALT-OKUNUR ve kapsam cevapta yazar: tek adayli kismi ad ("Goktürk") faaliyet/proje icin kabul edilir,
    # varsayim ayrica belirtilir. KISI/CARI adi asla sessizce secilmez ("Devrim" -> "Devrim Yildiz" baska kisi olabilir);
    # birden cok aday varsa da tahmin yurutulmez (asagida netlestirme istenir).
    for alan in ("faaliyet", "proje"):
        adaylar = n.oneri.get(alan)
        if adaylar and len(adaylar) == 1 and alan not in n.belirsiz and getattr(s, alan) is None:
            setattr(s, alan, adaylar[0])
            n.oneri.pop(alan)
            s.notlar.append(f"Sorudaki ad {adaylar[0].gorunen()} olarak anlaşıldı (tam eşleşme yoktu); yanlışsa tam adı yazın.")
    if s.faaliyet and s.faaliyet.kimlik == "touche_holding":
        s.faaliyet = None           # "Holding" = konsolide gorunum (alt faaliyetler dahil)
    s.belirsiz = {**n.belirsiz, **n.oneri}
    # Yon: "Isveren ne kadar odedi" = musterinin odemesi = bizim TAHSILATIMIZ. "Isverene ne kadar odedik" (yonelme eki) iadedir.
    if (s.metrik == "ODENEN" and s.cari and any(k in norm(s.cari.ekle("cari_tipi")) for k in ("musteri", "isveren"))
            and not N.yonelme_eki_var(str(metin), s.cari.ad)):
        s.metrik = "TAHSIL"
        s.notlar.append(f"{s.cari.ad} müşteri/işveren olduğu için sorudaki ödeme, tahsilat olarak anlaşıldı.")
    import finans_danismani as fd

    donem = cfo_dil.donem_cozumle(metin, bugun)         # "bu hafta", "son 30 gun", "ilk ceyrek", "1-10 Ekim" ...
    bas, bit, etiket = donem if donem else fd._donem_coz(metin, bugun)
    s.bas, s.bit, s.donem_etiketi = bas, bit, etiket
    s.detay = (fd._detay_istegi(metin) or _liste_detay_istegi(sade)
               or bool(N.COGUL_FINANS_RE.search(sade) and not re.search(r"\bne kadar\b|\bkac\b|\btoplam\b", sade)))
    return s


def _liste_detay_istegi(sade: str) -> bool:
    """'Bütün Devrim ödemelerini yaz' gibi okuma komutlarında toplamla yetinme, kalemleri de göster."""
    finans_nesnesi = re.search(r"\b(odeme|odemeler|tahsilat|gider|gelir|alacak|borc|kayit|hareket)\w*\b", sade)
    liste_baglam = re.search(r"\b(butun|tum|tüm|tam|liste|kalem kalem|dokum|ayrinti)\b", sade)
    liste_fiili = re.search(r"\b(yaz|listele|goster|sirala|dok|cikar|ver)\w*\b", sade)
    cogul_nesne = re.search(r"\b\w+(?:leri|lari|larini|lerini|ler|lar)\b", sade)
    return bool(finans_nesnesi and liste_fiili and (liste_baglam or cogul_nesne))


def _kapsam_metni(s: Sorgu) -> str:
    parcalar = []
    if s.faaliyet:
        parcalar.append(s.faaliyet.gorunen())
    if s.proje:
        parcalar.append(s.proje.ad)
    if s.cari:
        parcalar.append(s.cari.ad)
    return " / ".join(parcalar) if parcalar else "Holding geneli (konsolide)"


def _filtrele(fisler, s: Sorgu):
    sonuc = []
    for f in fisler.values():
        if s.faaliyet and f["faaliyet"] != s.faaliyet.kimlik:
            continue
        if s.proje and norm(f["proje"]) != norm(s.proje.ad):
            continue
        if s.cari and norm(f["cari"]) != norm(s.cari.ad):
            continue
        if s.bas and f["tarih"] < s.bas.isoformat():
            continue
        if s.bit and f["tarih"] > s.bit.isoformat():
            continue
        sonuc.append(f)
    return sonuc


def calistir(s: Sorgu) -> dict:
    """Sorguyu SALT-OKUNUR defterde hesaplar; yapisal sonuc dondurur."""
    metrik = s.metrik or ""
    anahtar, baslik, etiket = METRIKLER[metrik]
    with pf.okuma_baglantisi() as con:
        fisler, _ = pf._satirlari_oku(con, s.faaliyet.kimlik if s.faaliyet else None)
    secili = _filtrele(fisler, s)
    ham = pf.hesapla(secili)
    deger = ham[anahtar]
    ilgili = [f for f in secili if f["durum"] == "MUHASEBELESTI" and not f["ic"]
              and pf.fis_sinifi(f) in {"ODENEN": ("Ödeme", "Nakit gider"), "TAHSIL": ("Tahsilat",),
                                       "GIDER": ("Gider tahakkuku", "Nakit gider"),
                                       "KAZANC": ("Hakediş / gelir tahakkuku", "Nakit gelir")}.get(metrik, ())]
    kalemler = [{"tarih": f["tarih"], "fis_id": f["fis_id"], "cari": f["cari"], "proje": f["proje"],
                 "tutar_kurus": max((max(x[2], x[3]) for x in f["satirlar"]), default=0),
                 "aciklama": f["aciklama"][:120]} for f in sorted(ilgili, key=lambda x: (x["tarih"], x["fis_id"]))]
    ek = {}
    if s.metrik == "KAZANC":
        ek["tahsil_edilen_kurus"], ek["kalan_alacak_kurus"] = ham["tahsilat"], ham["kalan_alacak"]
    if s.metrik == "ODENEN":
        ek["gider_kurus"], ek["acik_borc_kurus"] = ham["gider"], ham["acik_borc"]
    if s.metrik == "KAR":
        ek["gelir_kurus"], ek["gider_kurus"] = ham["gelir"], ham["gider"]
    return {
        "metrik": s.metrik, "baslik": baslik, "etiket": etiket, "deger_kurus": int(deger),
        "adet": len(kalemler), "kalemler": kalemler, "ek": ek,
        "kapsam": _kapsam_metni(s), "donem": s.donem_etiketi,
        "filtre": {"faaliyet": s.faaliyet.kimlik if s.faaliyet else None, "proje": s.proje.ad if s.proje else None,
                   "cari": s.cari.ad if s.cari else None, "bas": s.bas.isoformat() if s.bas else None,
                   "bit": s.bit.isoformat() if s.bit else None},
        "kaynak": "merkezi defter (salt-okunur)", "veri_tarihi": datetime.now().isoformat(timespec="seconds"),
    }


def _tl(kurus):
    return tl_metin(int(kurus) / 100, kusuratsiz=False)


def acikla(sonuc: dict, detay=False) -> str:
    """Sablon aciklama: yalnizca hesaplanmis sayilari kullanir."""
    k = sonuc
    satirlar = [f"{k['kapsam']} · {k['donem']}: {k['etiket']} {_tl(k['deger_kurus'])}."]
    if k["metrik"] in ("ODENEN", "TAHSIL", "GIDER", "KAZANC") and k["adet"]:
        satirlar[0] = satirlar[0][:-1] + f" ({k['adet']} kayıt)."
    if k["metrik"] == "KAZANC":
        satirlar.append(f"Bunun {_tl(k['ek']['tahsil_edilen_kurus'])} kadarı tahsil edildi; kalan alacak {_tl(k['ek']['kalan_alacak_kurus'])}. "
                        "(Kazanç ile tahsilat aynı şey değildir.)")
    if k["metrik"] == "ODENEN":
        satirlar.append(f"Bu dönemde/kapsamda kayıtlı gider {_tl(k['ek']['gider_kurus'])}; açık borç {_tl(k['ek']['acik_borc_kurus'])}. "
                        "(Ödeme ile gider aynı şey değildir.)")
        if k["ek"]["acik_borc_kurus"] < 0:
            satirlar.append("Uyarı: ödemeye karşılık gider tahakkuku eksik görünüyor; maliyet/kâr eksik hesaplanabilir.")
    if k["metrik"] == "KAR":
        satirlar.append(f"Gelir {_tl(k['ek']['gelir_kurus'])} − gider {_tl(k['ek']['gider_kurus'])}. (Kâr nakit akışı değildir.)")
    if not k["adet"] and k["metrik"] in ("ODENEN", "TAHSIL", "GIDER", "KAZANC") and k["deger_kurus"] == 0:
        satirlar.append("Bu kapsamda/dönemde kayıt bulunamadı.")
    if detay and k["kalemler"]:
        for i, x in enumerate(k["kalemler"][:30], 1):
            gun = datetime.strptime(x["tarih"], "%Y-%m-%d").strftime("%d.%m.%Y")
            satirlar.append(f"{i}. {gun} · {x['cari'] or 'Cari yok'} · {_tl(x['tutar_kurus'])}"
                            + (f" · {x['aciklama']}" if x["aciklama"] else ""))
    return "\n".join(satirlar)


def kayit_gecmisi(kimlik: str) -> dict:
    """'Bu odeme neden burada?': denetim zincirinden ilgili kaydin hikayesini cikarir."""
    import guvenlik

    yol = guvenlik.AUDIT_LOG
    olaylar = []
    try:
        for satir in open(yol, encoding="utf-8"):
            if kimlik.lower() in satir.lower():
                olaylar.append(json.loads(satir))
    except FileNotFoundError:
        pass
    taslak_idleri = {o.get("taslak_id") for o in olaylar if o.get("taslak_id")}
    for satir in _tum_satirlar(yol):
        if satir.get("taslak_id") in taslak_idleri and satir not in olaylar:
            olaylar.append(satir)
    olaylar.sort(key=lambda o: o.get("zaman", ""))
    return {"kimlik": kimlik, "olaylar": olaylar}


def _tum_satirlar(yol):
    try:
        return [json.loads(x) for x in open(yol, encoding="utf-8") if x.strip()]
    except FileNotFoundError:
        return []


def gecmis_acikla(g: dict) -> str:
    if not g["olaylar"]:
        return f"{g['kimlik']} için denetim kaydı bulunamadı."
    adimlar = []
    for o in g["olaylar"]:
        ad = {"cfo_taslak_olustu": "Talimat alındı", "cfo_taslak_adim": "Alan tamamlandı",
              "cfo_taslak_hazir": "Taslak hazırlandı", "excel_islem": "Excel'e yazıldı",
              "cfo_taslak_tekrar_engellendi": "Tekrar talimat engellendi"}.get(o.get("olay"), o.get("olay"))
        ek = o.get("orijinal_talimat") or (f"{o.get('alan')}={o.get('deger')}" if o.get("alan") else "")
        adimlar.append(f"{o['zaman'][:19]} · {ad}" + (f" · {ek}" if ek else "") + (f" · aktör {o['aktor']}" if o.get("aktor") else ""))
    return f"{g['kimlik']} kaydının geçmişi:\n" + "\n".join(adimlar)


def cevapla(metin, katalog: Katalog | None = None, bugun: date | None = None, aktor="") -> dict | None:
    """Sorguyu cevaplar; desteklenmiyorsa None doner (cagiran mevcut danismana devreder)."""
    katalog = katalog or katalog_yukle()
    s = sorgu_coz(metin, katalog, bugun)
    if s is None:
        return None
    if s.metrik == "KAYIT_GECMISI":
        g = kayit_gecmisi(s.kayit_kimligi)
        yanit_metni, sonuc = gecmis_acikla(g), g
    else:
        if s.belirsiz:
            adaylar = sorted({v.gorunen() for lst in s.belirsiz.values() for v in lst})
            secenekler = ", ".join(adaylar[:6])
            if len(adaylar) == 1:
                metin = (f"Sorudaki adla yalnızca şu kayıt eşleşebiliyor: {secenekler}. Bunu mu kastettiniz? "
                         "Tam adıyla yazarsanız cevaplarım; tahmin yürütmedim.")
            else:
                metin = f"Sorudaki ad birden fazla kayıtla eşleşiyor ({secenekler}). Hangisini kastettiğinizi belirtin; tahmin yürütmedim."
            return {"cevap": metin,
                    "niyet": "cfo_sorgu_netlestir", "llm": "deterministik", "guven": "Yüksek", "kanitlar": [],
                    "aksiyonlar": [], "degisiklik_yapildi": False}
        sonuc = calistir(s)
        yanit_metni = acikla(sonuc, s.detay)
        if s.notlar:
            yanit_metni += "\n" + "\n".join(f"Not: {x}" for x in s.notlar)
    audit_yaz("cfo_sorgu", aktor=str(aktor), metrik=s.metrik, kapsam=s.faaliyet.kimlik if s.faaliyet else None,
              soru_hash=hashlib.sha256(metin.encode("utf-8")).hexdigest())
    kanitlar = []
    if s.metrik != "KAYIT_GECMISI":
        kanitlar = [f"Kapsam: {sonuc['kapsam']}", f"Dönem: {sonuc['donem']}", f"Kayıt sayısı: {sonuc['adet']}",
                    f"Kaynak: {sonuc['kaynak']} · {sonuc['veri_tarihi'][:16]}"]
    return {"cevap": yanit_metni, "niyet": "cfo_sorgu", "llm": "deterministik", "guven": "Yüksek",
            "kanitlar": kanitlar, "aksiyonlar": [], "degisiklik_yapildi": False, "sorgu": sonuc,
            "baglam_sorusu": metin}
