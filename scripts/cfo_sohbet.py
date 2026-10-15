# -*- coding: utf-8 -*-
"""CFO sohbet sihirbazi: yapisal taslak durumu + her seferinde TEK eksik alan sorusu.

Mimari:  kullanici -> cfo_niyet (deterministik cikarim) -> bu modul (yapisal taslak, eksik alan,
secenekler) -> dogrulama (dogrulayici) -> MEVCUT onay kuyrugu (ui_belge_servisi, sifreli onay)
-> excel_yazici -> excel_deposu.

* LLM cagrilmaz. Bir secenege tiklamak veya serbest cevap yazmak deterministik islenir.
* Bilinen alanlar korunur; sihirbaz hicbir cevaptan sonra basa donmez.
* Hicbir varlik (faaliyet/proje/cari/hesap/is kalemi) uydurulmaz; secenekler gercek tanimlardandir.
* Excel'e yalniz onay kuyrugu uzerinden, sifreyle yazilir; burada yazma YOKTUR.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import config
import cfo_eslestirme as ES
import cfo_niyet as N
import proje_finansi as pf
from cfo_varliklar import BELIRSIZ, ONERI, TEKIL, Katalog, coz, katalog_yukle, kismi_coz, norm
from dogrulayici import FAIL, deterministik_dogrula
from guvenlik import audit_yaz
from master_data import master_data_oku

TOPLANIYOR, ONAY_BEKLIYOR, TAMAMLANDI, IPTAL = "TOPLANIYOR", "ONAY_BEKLIYOR", "TAMAMLANDI", "IPTAL"
GENEL, YENI, YOK_KALEM = "__GENEL__", "__YENI__", "__YOK__"
MOTOR = "deterministik_cfo"
_LOCK = threading.RLock()
_TASLAKLAR: dict = {}
STATE_PATH = Path(config.LOGLAR) / "cfo_taslaklari.json"


# ---------------------------------------------------------------------------
# Kalicilik
# ---------------------------------------------------------------------------

def _yukle():
    global _TASLAKLAR
    try:
        veri = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        _TASLAKLAR = veri if isinstance(veri, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        _TASLAKLAR = {}


def _kaydet():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    gecici = STATE_PATH.with_suffix(".tmp")
    gecici.write_text(json.dumps(_TASLAKLAR, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(gecici, STATE_PATH)


def sifirla(yol=None):
    """Test yardimcisi: bellek ve (isteğe bagli) dosya yolunu sifirlar."""
    global STATE_PATH
    with _LOCK:
        if yol is not None:
            STATE_PATH = Path(yol)
        _TASLAKLAR.clear()


_yukle()


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _simdi():
    return datetime.now().isoformat(timespec="seconds")


def tl_metin(tutar, pb="TL") -> str:
    d = Decimal(str(tutar))
    metin = f"{d:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if metin.endswith(",00"):
        metin = metin[:-3]
    return f"{metin} {pb}"


def komut_anahtari(metin, aktor, bugun=None) -> str:
    """Ayni kullanicinin ayni gun yazdigi ayni talimat icin kararli anahtar."""
    bugun = bugun or date.today()
    return hashlib.sha256(f"{norm(metin)}|{aktor}|{bugun.isoformat()}".encode("utf-8")).hexdigest()[:24]


def _kayitli_getir(taslak_id):
    t = _TASLAKLAR.get(str(taslak_id or ""))
    if not t:
        raise ValueError("CFO taslağı bulunamadı veya süresi doldu. Talimatı yeniden yazın.")
    return t


def getir(taslak_id):
    with _LOCK:
        return json.loads(json.dumps(_kayitli_getir(taslak_id)))


def _adim_kaydet(t, alan, deger, kaynak):
    t.setdefault("adimlar", []).append({"zaman": _simdi(), "alan": alan, "deger": str(deger)[:200], "kaynak": kaynak})
    audit_yaz("cfo_taslak_adim", taslak_id=t["id"], alan=alan, deger=str(deger)[:200], kaynak=kaynak,
              aktor=t.get("aktor"))


def _bos_taslak(metin, aktor, tekrar_no=0, bugun=None):
    return {
        "id": uuid.uuid4().hex[:24], "durum": TOPLANIYOR, "aktor": str(aktor), "olusturma": _simdi(),
        "guncelleme": _simdi(), "kaynak_metin": metin, "komut_anahtari": komut_anahtari(metin, aktor, bugun),
        "tekrar_no": tekrar_no, "faaliyet": None, "proje": None, "islem": None, "kalem_id": None,
        "kalem_ad": None, "kalem_yok": False, "cari": None, "cari_yeni": False, "cari_tipi": None,
        "tutar": None, "pb": None, "pb_acik": False, "tarih": None, "tarih_kaynagi": "", "hesap": None,
        "hesap_kaynagi": "", "hesap_ipucu": "", "kur": None, "ortak_odemesi": False, "notlar": [], "adimlar": [],
        "bekleyen_alan": None, "bekleyen_yeni_cari": None, "onay_token": None, "belge_id": None,
        "belirsiz": {}, "oneri": {}, "tarih_hatasi": "", "kapsam": None,
        "baglanti": None, "baglanti_karari": None, "eslesme": None, "mukerrer_onay": False, "yine_de": False,
    }


def _niyetten_uygula(t, n: N.Niyet, katalog: Katalog):
    """Cikarilan niyeti taslaga yazar; zaten bilinen (kullanicinin onayladigi) alanlari ezmez."""
    if n.faaliyet and not t["faaliyet"]:
        if _kapsamda(t, n.faaliyet.kimlik):
            t["faaliyet"] = n.faaliyet.kimlik
        else:
            t["notlar"].append("Metindeki faaliyet için yetkiniz yok; faaliyet yeniden sorulacak.")
    if n.proje and not t["proje"]:
        t["proje"] = n.proje.ad
    if n.kalem and not t["kalem_id"]:
        t["kalem_id"], t["kalem_ad"] = n.kalem.kimlik, n.kalem.ad
    if n.cari and not t["cari"]:
        t["cari"], t["cari_tipi"] = n.cari.ad, n.cari.ekle("cari_tipi")
    elif n.yeni_cari_adi and not t["cari"]:
        t["bekleyen_yeni_cari"] = n.yeni_cari_adi
    if n.hesap and not t["hesap"]:
        t["hesap"], t["hesap_kaynagi"] = n.hesap.ad, "metin"
    t["hesap_ipucu"] = t.get("hesap_ipucu") or n.hesap_ipucu
    if n.islem and not t["islem"]:
        t["islem"] = n.islem
    if n.tutar and not t["tutar"]:
        t["tutar"] = str(n.tutar)
    if n.pb and not t["pb"]:
        t["pb"], t["pb_acik"] = n.pb, True
    if n.tarih and not t["tarih"]:
        t["tarih"], t["tarih_kaynagi"] = n.tarih.isoformat(), n.tarih_kaynagi
    t["tarih_hatasi"] = n.tarih_hatasi or ""
    t["ortak_odemesi"] = t["ortak_odemesi"] or n.ortak_odemesi
    t["belirsiz"] = {a: [{"tur": v.tur, "kimlik": v.kimlik, "ad": v.ad, "ust": v.ust} for v in lst]
                     for a, lst in n.belirsiz.items()}
    t["oneri"] = {a: [{"tur": v.tur, "kimlik": v.kimlik, "ad": v.ad, "ust": v.ust} for v in lst]
                  for a, lst in n.oneri.items()}
    for not_ in n.notlar:
        if not_ not in t["notlar"]:
            t["notlar"].append(not_)


# ---------------------------------------------------------------------------
# Eksik alan ve soru uretimi
# ---------------------------------------------------------------------------

def _kapsamda(t, faaliyet_kimlik) -> bool:
    """Oturumun yetkili faaliyetleri disindaki faaliyet secilemez (sunucu tarafi kapsam)."""
    kapsam = t.get("kapsam")
    return not kapsam or "*" in kapsam or faaliyet_kimlik in kapsam


def _faaliyet(t, katalog):
    return katalog.faaliyet(t["faaliyet"]) if t["faaliyet"] else None


def _ortak(t) -> bool:
    """Ortak cari odemesi: metinde 'ortak' gecmesi YA DA odemeyi alan carinin tipinin 'Ortak' olmasi."""
    return bool(t["ortak_odemesi"] or "ortak" in norm(t.get("cari_tipi") or ""))


def _islem_turu(t, katalog):
    if not t["islem"]:
        return None
    if t["islem"] in (N.PAYMENT, N.COUNTERPARTY_PAYMENT) and "banka" in norm(t.get("cari_tipi") or ""):
        mevcut = {norm(x): x for x in katalog.islem_turleri}
        if norm("Kredi Taksit Ödemesi") in mevcut:
            return mevcut[norm("Kredi Taksit Ödemesi")]
    return N.islem_turu_bul(t["islem"], katalog.islem_turleri, _ortak(t))


def _kalem_gerekli(t, katalog) -> bool:
    return t["islem"] == N.WORK_ITEM_PAYMENT


def _kalem_sorulabilir(t, katalog) -> bool:
    """Gider/proje gideri icin projede is kalemi varsa (opsiyonel) sorulur."""
    f = _faaliyet(t, katalog)
    return bool(f and t["proje"] and t["islem"] in (N.EXPENSE, N.PROJECT_COST, N.PAYABLE)
                and katalog.kalemleri(f.kimlik, t["proje"]) and not t["kalem_id"] and not t["kalem_yok"])


def _baglanti_turu(t):
    """Acik borc/alacakla eslesmesi gereken islemler: BORC (cari/is kalemi odemesi), ALACAK (tahsilat/alacak silme)."""
    if _ortak(t):
        return None                       # ortak cari hesabi tedarikci/usta borcu degildir
    if t["islem"] in (N.COUNTERPARTY_PAYMENT, N.WORK_ITEM_PAYMENT):
        return ES.BORC
    if t["islem"] in (N.COLLECTION, N.RECEIVABLE_ADJUSTMENT):
        return ES.ALACAK
    return None


def _baglanti_gerekli(t, katalog) -> bool:
    return bool(_baglanti_turu(t) and t["faaliyet"] and t["proje"] and t["cari"] and t["tutar"]
                and any(norm(p.ad) == norm(t["proje"]) for p in katalog.projeleri(t["faaliyet"])))


def _proje_opsiyonel(t) -> bool:
    """Kredi/banka ödemelerinde proje zorunlu değildir; kredi/taksit kodu açıklamada kanıt olarak kalır."""
    return bool(t["islem"] in (N.PAYMENT, N.COUNTERPARTY_PAYMENT)
                and "banka" in norm(t.get("cari_tipi") or ""))


def _tutar_kurus(t, katalog) -> int:
    kur = _kur(t, katalog) or Decimal(1)
    return int((Decimal(t["tutar"]) * kur * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _eslesme_hesapla(t, katalog) -> dict:
    """Defterdeki acik borc/alacakla eslesme (salt-okunur). Defter okunamazsa EMIN OLUNAMAZ: tahmin yok, sorulur."""
    try:
        f = _faaliyet(t, katalog)
        kalemler = [(k.ad, k.ekle("grup") or "") for k in katalog.kalemleri(f.kimlik, t["proje"])]
        kalem = next(((k.ad, k.ekle("grup") or "") for k in katalog.kalemleri(f.kimlik, t["proje"])
                      if k.kimlik == t["kalem_id"]), None) if t["kalem_id"] else None
        return ES.eslestir(t["faaliyet"], t["proje"], t["cari"], _tutar_kurus(t, katalog), _baglanti_turu(t),
                           t["kaynak_metin"], kalem, kalemler)
    except Exception as exc:
        audit_yaz("cfo_eslesme_hatasi", taslak_id=t["id"], hata=str(exc)[:200], aktor=t.get("aktor"))
        return {"karar": "YOK", "adaylar": [], "acik_kalemler": [], "hata": str(exc)[:200]}


def _kurus_metin(kurus) -> str:
    return tl_metin(Decimal(int(kurus)) / 100)


def _baglanti_cumlesi(b) -> str:
    kelime = "borcu" if b["tur"] == ES.BORC else "alacağı"
    return (f"{b['cari']} / {b['proje']} açık {kelime} kapatılıyor "
            f"(kalan {_kurus_metin(b['kalan_kurus'])} → {_kurus_metin(b['kalan_sonra_kurus'])})")


def _baglanti_ata(t, aday, kaynak, katalog):
    tutar = _tutar_kurus(t, katalog)
    t["baglanti"] = {"tur": _baglanti_turu(t), "cari": aday["cari"], "proje": aday["proje"],
                     "kalan_kurus": aday["kalan_kurus"], "kalan_sonra_kurus": max(aday["kalan_kurus"] - tutar, 0),
                     "skor": aday.get("skor", 0), "kanit": list(aday.get("kanit", [])), "kaynak": kaynak,
                     "asiyor": tutar > aday["kalan_kurus"]}
    t["baglanti_karari"] = "kapat"
    not_ = "Açık kalem bağlantısı: " + _baglanti_cumlesi(t["baglanti"])
    if not_ not in t["notlar"]:
        t["notlar"].append(not_)


def _baglanti_sifirla(t):
    """Eslesmeyi etkileyen bir alan degisti: baglanti ve mukerrer onayi yeniden hesaplanir."""
    t["baglanti"], t["baglanti_karari"], t["eslesme"], t["mukerrer_onay"] = None, None, None, False
    t["notlar"] = [x for x in t["notlar"] if not x.startswith("Açık kalem bağlantısı:")
                   and not x.startswith("Kapatılacak açık")]


def _benzer_fisler(t, katalog) -> list[str]:
    """Ayni gun/cari/proje/tutarli MUHASEBELESTI fisler (en fazla 3). Defter okunamazsa [] (bilgi eksikligi engel degildir)."""
    try:
        import sqlite3

        import finans_cekirdegi as cek

        if not Path(cek.DB_PATH).exists() or not (t["tutar"] and t["tarih"] and t["cari"]):
            return []
        kurus = _tutar_kurus(t, katalog)
        con = sqlite3.connect(f"file:{cek.DB_PATH}?mode=ro", uri=True)
        try:
            return [x[0] for x in con.execute(
                "SELECT DISTINCT f.fis_id FROM fisler f JOIN fis_satirlari s ON s.fis_id=f.fis_id "
                "WHERE f.faaliyet_key=? AND f.tarih=? AND f.durum='MUHASEBELESTI' AND f.cari=? AND f.proje=? "
                "AND (s.borc_kurus=? OR s.alacak_kurus=?) LIMIT 3",
                (t["faaliyet"], t["tarih"], t["cari"], t["proje"] or "", kurus, kurus)).fetchall()]
        finally:
            con.close()
    except Exception:
        return []


def _nakit_gerekli(t, katalog) -> bool:
    tur = _islem_turu(t, katalog)
    return bool(tur and N.nakit_gerekli_mi(tur))


def _pb_coz(t, katalog):
    """Para birimi: metindeki acik ifade > secilen hesabin para birimi > TL."""
    if t["pb"]:
        return t["pb"]
    if t["hesap"]:
        h = next((x for x in katalog.hesaplar if x.ad == t["hesap"] and x.ust == t["faaliyet"]), None)
        if h:
            return (h.ekle("pb") or "TL").upper().replace("TRY", "TL")
    return "TL"


def _hesap_adaylari(t, katalog):
    pb = _pb_coz(t, katalog)
    adaylar = katalog.hesaplari(t["faaliyet"])
    return [h for h in adaylar if (h.ekle("pb") or "TL").upper().replace("TRY", "TL") == pb] or []


def _hesap_otomatik(t, katalog):
    """Hesap tahmini YALNIZ kanitli ise yapilir (tek uygun hesap veya ayni cari+islem gecmisi)."""
    adaylar = _hesap_adaylari(t, katalog)
    if len(adaylar) == 1:
        return adaylar[0].ad, f"otomatik: {t['faaliyet']} faaliyetinde {_pb_coz(t, katalog)} için tek hesap"
    ipucu = t.get("hesap_ipucu")
    if ipucu:
        # Metindeki "kasadan/elden" ya da "bankadan/havale" ipucu: yalniz TEK uygun hesap kaliyorsa (secenekler daraltilmaz)
        kelime = "kasa" if ipucu == "KASA" else "banka"
        daraltilmis = [h for h in adaylar if kelime in norm(h.ad)]
        if len(daraltilmis) == 1:
            return daraltilmis[0].ad, f"otomatik: ifadedeki '{kelime}' ipucu ({_pb_coz(t, katalog)})"
    f = _faaliyet(t, katalog)
    tur = _islem_turu(t, katalog)
    if f and t["cari"] and tur:
        gecmis = [g for g in katalog.md.get("kayit_gecmisi", [])
                  if norm(g.get("faaliyet")) == norm(f.ad) and norm(g.get("cari")) == norm(t["cari"])
                  and norm(g.get("islem_turu")) == norm(tur) and (g.get("pb") or "TL").upper() == _pb_coz(t, katalog)]
        hesaplar = {g["hesap_kasa"] for g in gecmis if g.get("hesap_kasa")}
        gecerli = {h.ad for h in adaylar}
        if len(hesaplar) == 1 and next(iter(hesaplar)) in gecerli:
            return next(iter(hesaplar)), f"otomatik: {t['cari']} için önceki {tur} kayıtlarındaki tek hesap"
    return None, ""


def eksik_alanlar(t, katalog) -> list[str]:
    """Sirayla eksik/belirsiz alanlar (spec sirasi: faaliyet, proje, islem, kalem/cari, tutar, pb, tarih, hesap)."""
    e = []
    if not t["faaliyet"]:
        e.append("faaliyet")
    f = _faaliyet(t, katalog)
    if f and not t["proje"] and not _proje_opsiyonel(t):
        e.append("proje")
    if t["islem"] == N.PAYMENT and f and t["proje"] and not katalog.kalemleri(f.kimlik, t["proje"]):
        # Projede is kalemi yok: ayrim sorusu anlamsiz; ödeme cari ödemesidir.
        t["islem"] = N.COUNTERPARTY_PAYMENT
        t["notlar"].append("Projede iş kalemi tanımı olmadığı için cari ödemesi kabul edildi.")
    if not t["islem"] or t["islem"] == N.PAYMENT:
        e.append("islem")
    elif _islem_turu(t, katalog) is None:
        e.append("islem")
    if t["islem"] and t["islem"] != N.PAYMENT:
        if _kalem_gerekli(t, katalog) and not t["kalem_id"]:
            e.append("kalem")
        elif _kalem_sorulabilir(t, katalog):
            e.append("kalem")
        if not t["cari"]:
            e.append("cari_yeni_onay" if t["bekleyen_yeni_cari"] else "cari")
        elif t["cari_yeni"] and not t["cari_tipi"]:
            e.append("cari_tipi")
    if not t["tutar"]:
        e.append("tutar")
    if not e and _baglanti_gerekli(t, katalog) and t.get("baglanti_karari") is None:
        sonuc = _eslesme_hesapla(t, katalog)
        if sonuc["karar"] == "ESLESTI":                    # tek, kanitli aday: otomatik (onay sifresi yine gerekir)
            _baglanti_ata(t, sonuc["adaylar"][0], "otomatik", katalog)
        elif sonuc["karar"] == "ACIK_YOK":                 # kapatilacak acik kalem HIC yok: belirsizlik degil
            t["baglanti_karari"] = "yeni"
            t["notlar"].append("Kapatılacak açık "
                               + ("borç" if _baglanti_turu(t) == ES.BORC else "alacak")
                               + " bulunmadığı için yeni kayıt olarak hazırlandı.")
        else:
            t["eslesme"] = sonuc
            e.append("borc")
    if t["tarih_hatasi"] and not t["tarih"]:
        e.append("tarih")
    if t["islem"] and t["islem"] != N.PAYMENT and _nakit_gerekli(t, katalog) and not t["hesap"]:
        ad, neden = _hesap_otomatik(t, katalog)
        if ad:
            t["hesap"], t["hesap_kaynagi"] = ad, neden
            if neden not in t["notlar"]:
                t["notlar"].append(neden)
        else:
            e.append("hesap")
    if _pb_coz(t, katalog) != "TL" and not _kur(t, katalog):
        e.append("kur")
    if not e and not (t.get("yine_de") or t.get("mukerrer_onay")) and _benzer_fisler(t, katalog):
        e.append("mukerrer_onay")                          # mukerrer ihtimali: taslak acmadan once sorulur
    return e


def _kur(t, katalog):
    pb = _pb_coz(t, katalog)
    if pb == "TL":
        return Decimal(1)
    if t.get("kur"):
        return Decimal(str(t["kur"]))
    kur = katalog.kurlar.get(pb)
    try:
        return Decimal(str(kur)) if kur not in (None, "") and Decimal(str(kur)) > 0 else None
    except Exception:
        return None


def _secenek(id_, etiket, aciklama=""):
    return {"id": id_, "etiket": etiket, "aciklama": aciklama}


def _borc_sorusu(t, katalog) -> dict:
    tur = _baglanti_turu(t)
    kelime = "borcu" if tur == ES.BORC else "alacağı"
    s = _eslesme_hesapla(t, katalog)
    t["eslesme"] = s

    def ad(a):
        return f"{a['cari']} / {a['proje']} / kalan {_kurus_metin(a['kalan_kurus'])}"

    if s["karar"] == "ONAY":
        a = s["adaylar"][0]
        uyari = " Dikkat: tutar kalanı aşıyor; fazlası avans olarak kalır." if a["asiyor"] else ""
        metin = (f"Emin değilim. Bu işlem şu açık {kelime} kapatıyor olabilir: {ad(a)}. "
                 f"Gerekçe: {'; '.join(a['kanit'])}.{uyari} Bu {kelime} kapatayım mı, yoksa yeni kayıt mı açalım?")
        secenekler = [_secenek("kapat:" + a["cari"], f"Evet, {a['cari']} {kelime} kapat", ad(a)),
                      _secenek("yeni", "Hayır, yeni kayıt aç")]
    elif s["karar"] == "BELIRSIZ":
        metin = "Emin değilim, şu açık kayıtlar var; hangisini kapatayım?"
        secenekler = [_secenek("kapat:" + a["cari"], ad(a), "; ".join(a["kanit"])) for a in s["adaylar"][:6]]
        secenekler.append(_secenek("yeni", "Hiçbiri — yeni kayıt aç"))
    else:
        metin = ("Hiç eşleşme bulamadım. Bu işlem mevcut bir borç/alacak/hakediş kaydını mı kapatıyor, "
                 "yoksa yeni bir kayıt mı açalım?")
        if s.get("hata"):
            metin = "Mevcut açık kayıtlara erişemedim, bu yüzden emin olamıyorum. " + metin
        secenekler = [_secenek("kapat:" + a["cari"], f"{ad(a)} kaydını kapatıyor") for a in s["acik_kalemler"][:6]]
        secenekler.append(_secenek("yeni", "Yeni kayıt aç"))
    return {"alan": "borc", "metin": metin, "secenekler": secenekler}


def soru_uret(t, alan, katalog: Katalog) -> dict:
    if alan == "faaliyet":
        adaylar = t["belirsiz"].get("faaliyet")
        liste = [katalog.faaliyet(a["kimlik"]) for a in adaylar] if adaylar else katalog.faaliyetler
        liste = [x for x in liste if x and _kapsamda(t, x.kimlik)]
        return {"alan": alan, "metin": "Hangi faaliyet için?",
                "secenekler": [_secenek(x.kimlik, x.gorunen()) for x in liste]}
    if alan == "proje":
        projeler = katalog.projeleri(t["faaliyet"])
        secenekler = [_secenek(p.ad, p.ad) for p in projeler]
        genel = [p for p in master_data_oku(t["faaliyet"])["projeler"] if norm(p) not in {norm(x.ad) for x in projeler}]
        secenekler += [_secenek(GENEL, "Faaliyet geneli (projesiz)", g) for g in genel[:1]]
        metin = "Hangi proje?"
        if not secenekler:
            metin = "Bu faaliyette tanımlı proje yok. Önce Excel'de (99_Ayarlar) veya Telegram'da proje ekleyin."
        return {"alan": alan, "metin": metin, "secenekler": secenekler}
    if alan == "islem":
        if t["islem"] == N.PAYMENT:
            secenekler = [_secenek(N.COUNTERPARTY_PAYMENT, "Cari ödemesi", "Bir kişiye/firmaya ödeme"),
                          _secenek(N.WORK_ITEM_PAYMENT, "İş kalemi ödemesi", "Projenin bir iş kalemi için ödeme")]
            if not katalog.kalemleri(t["faaliyet"], t["proje"]):
                secenekler = secenekler[:1]
            return {"alan": alan, "metin": "Bu ödeme bir iş kalemine mi, yoksa cari ödemesi mi?", "secenekler": secenekler}
        if t["islem"] and _islem_turu(t, katalog) is None:
            return {"alan": alan, "metin": f"'{N.ETIKET.get(t['islem'], t['islem'])}' için çalışma kitabında işlem türü tanımlı değil. Hangisi?",
                    "secenekler": [_secenek(x, x) for x in katalog.islem_turleri]}
        return {"alan": alan, "metin": "Ne tür bir işlem?",
                "secenekler": [_secenek(k, e, a) for k, e, a in N.ISLEM_SECENEKLERI]}
    if alan == "kalem":
        kalemler = katalog.kalemleri(t["faaliyet"], t["proje"])
        secenekler = [_secenek(k.kimlik, k.ad, k.ekle("grup") or "") for k in kalemler]
        if not _kalem_gerekli(t, katalog):
            secenekler.append(_secenek(YOK_KALEM, "İş kalemi yok / genel"))
        return {"alan": alan, "metin": "Hangi iş kalemi?", "secenekler": secenekler}
    if alan == "cari":
        carileri = katalog.carileri(t["faaliyet"])
        musteri_islem = t["islem"] in (N.COLLECTION, N.RECEIVABLE, N.PROJECT_REVENUE, N.INCOME,
                                       N.RECEIVABLE_ADJUSTMENT)
        carileri = sorted(carileri, key=lambda c: ("musteri" in norm(c.ekle("cari_tipi"))) != musteri_islem)
        metin = {N.COLLECTION: "Kimden tahsil edildi?", N.RECEIVABLE: "Hangi müşteri/cari için?",
                 N.RECEIVABLE_ADJUSTMENT: "Hangi müşteri/cari alacağı siliniyor?",
                 N.INCOME: "Gelir hangi cariden?", N.EXPENSE: "Gider hangi cariye/tedarikçiye ait?",
                 N.PROJECT_COST: "Gider hangi cariye/tedarikçiye ait?"}.get(t["islem"], "Kime ödeme yapıldı?")
        secenekler = [_secenek(c.ad, c.ad, c.ekle("cari_tipi") or "") for c in carileri]
        secenekler.append(_secenek(YENI, "Listede yok — yeni kişi/firma ekle"))
        return {"alan": alan, "metin": metin, "secenekler": secenekler,
                "serbest": True, "ipucu": "Listeden seçin veya adı yazın."}
    if alan == "cari_yeni_onay":
        ad = t["bekleyen_yeni_cari"]
        return {"alan": alan, "metin": f"'{ad}' cari listesinde yok. Yeni cari olarak eklensin mi?",
                "secenekler": [_secenek("evet", f"Evet, '{ad}' yeni cari olsun"), _secenek("hayir", "Hayır, listeden seçeyim")]}
    if alan == "cari_tipi":
        return {"alan": alan, "metin": f"'{t['cari']}' hangi tipte bir cari?",
                "secenekler": [_secenek(x, x) for x in katalog.md.get("cari_tipler", [])]}
    if alan == "tutar":
        return {"alan": alan, "metin": "Tutar nedir? (örn. 10 bin TL, 12.500,50)", "secenekler": [], "serbest": True}
    if alan == "tarih":
        return {"alan": alan, "metin": f"{t['tarih_hatasi']} İşlem tarihi nedir? (örn. 12.10.2026, bugün, dün)",
                "secenekler": [_secenek("bugun", "Bugün"), _secenek("dun", "Dün")], "serbest": True}
    if alan == "hesap":
        hesaplar = _hesap_adaylari(t, katalog)
        pb = _pb_coz(t, katalog)
        metin = "Hangi hesap/kasadan?" if t["islem"] not in (N.COLLECTION, N.INCOME) else "Hangi hesap/kasaya girdi?"
        if not hesaplar:
            metin = f"Bu faaliyette {pb} para birimli hesap/kasa tanımlı değil."
        return {"alan": alan, "metin": metin, "secenekler": [_secenek(h.ad, h.ad, pb) for h in hesaplar]}
    if alan == "borc":
        return _borc_sorusu(t, katalog)
    if alan == "mukerrer_onay":
        benzer = ", ".join(_benzer_fisler(t, katalog))
        return {"alan": alan,
                "metin": (f"Aynı gün, aynı cari, aynı proje ve aynı tutarla bir kayıt zaten var ({benzer}). "
                          "Bu mükerrer olabilir; yine de yeni bir kayıt taslağı hazırlansın mı?"),
                "secenekler": [_secenek("evet", "Yine de taslak hazırla"), _secenek("hayir", "Hayır, vazgeç")]}
    if alan == "kur":
        return {"alan": alan, "metin": f"{_pb_coz(t, katalog)} için kur tanımlı değil. 1 {_pb_coz(t, katalog)} kaç TL?",
                "secenekler": [], "serbest": True}
    raise ValueError(f"Bilinmeyen alan: {alan}")


# ---------------------------------------------------------------------------
# Cevap isleme (deterministik)
# ---------------------------------------------------------------------------

def _tutar_uygula(t, metin):
    tutarlar = N.tutar_cikar(metin)
    if not tutarlar:
        raise ValueError("Tutarı anlayamadım. Örnek: 10 bin TL veya 12.500,50")
    if len({(x["tutar"], x["pb"]) for x in tutarlar}) > 1:
        raise ValueError("Birden fazla tutar var; yalnızca tek bir tutar yazın.")
    t["tutar"] = str(tutarlar[0]["tutar"])
    if tutarlar[0]["pb"]:
        t["pb"], t["pb_acik"] = tutarlar[0]["pb"], True


def _entity_sec(adaylar, metin, ne):
    c = coz(metin, adaylar, bulanik=True)
    if c.durum == TEKIL:
        return c.secilen
    if not c.adaylar:
        # Aktif soruya "goktürk" gibi ayirt edici tek kelimeyle cevap verilebilir.
        kismi = kismi_coz(metin, adaylar)
        if len(kismi) == 1:
            return kismi[0]
        if len(kismi) > 1:
            raise ValueError(f"{ne} için birden fazla olası eşleşme var: " + ", ".join(x.gorunen() for x in kismi[:5])
                             + ". Lütfen listeden seçin.")
    if c.durum in (BELIRSIZ, ONERI):
        raise ValueError(f"{ne} için birden fazla olası eşleşme var: " + ", ".join(x.gorunen() for x in c.adaylar[:5])
                         + ". Lütfen listeden seçin.")
    raise ValueError(f"'{metin}' için {ne} bulunamadı. Lütfen listeden seçin.")


def _uygula(t, alan, deger, katalog: Katalog, kaynak: str):
    """Tek alan cevabini taslaga yazar. ``deger`` secenek kimligi veya serbest metindir."""
    deger = str(deger or "").strip()
    if not deger:
        raise ValueError("Boş cevap.")
    if alan in ("faaliyet", "proje", "islem", "kalem", "cari", "cari_yeni_onay", "cari_tipi", "tutar", "kur"):
        _baglanti_sifirla(t)                 # eslesmeyi etkileyen bir bilgi degisiyor
    if alan == "faaliyet":
        f = katalog.faaliyet(deger) if kaynak == "secim" else _entity_sec(
            [x for x in katalog.faaliyetler if _kapsamda(t, x.kimlik)], deger, "Faaliyet")
        if not f or not _kapsamda(t, f.kimlik):
            raise ValueError("Faaliyet bulunamadı veya bu faaliyet için yetkiniz yok.")
        if t["faaliyet"] != f.kimlik:
            t["proje"], t["kalem_id"], t["kalem_ad"], t["hesap"], t["cari"] = None, None, None, None, None
        t["faaliyet"] = f.kimlik
        t["belirsiz"].pop("faaliyet", None)
        valor = f.gorunen()
    elif alan == "proje":
        if deger == GENEL:
            genel = [p for p in master_data_oku(t["faaliyet"])["projeler"]
                     if norm(p) not in {norm(x.ad) for x in katalog.projeleri(t["faaliyet"])}]
            if not genel:
                raise ValueError("Bu faaliyette projesiz kayıt tanımı yok.")
            t["proje"] = valor = genel[0]
        else:
            adaylar = katalog.projeleri(t["faaliyet"])
            p = next((x for x in adaylar if x.ad == deger), None) if kaynak == "secim" else _entity_sec(adaylar, deger, "Proje")
            if not p:
                raise ValueError("Proje bu faaliyette bulunamadı.")
            t["proje"] = valor = p.ad
        t["kalem_id"], t["kalem_ad"] = None, None
    elif alan == "islem":
        secilen = deger if kaynak == "secim" else _islem_metinden(deger, katalog)
        if secilen in {x[0] for x in N.ISLEM_SECENEKLERI} | {N.PAYMENT, N.PROJECT_COST, N.PAYABLE}:
            t["islem"] = secilen
        elif secilen in katalog.islem_turleri:           # dogrudan kitap tanimi secildi
            t["islem"] = _islem_tipi_turden(secilen)
        else:
            raise ValueError("İşlem türünü anlayamadım. Listeden seçin.")
        valor = N.ETIKET.get(t["islem"], t["islem"])
    elif alan == "kalem":
        if deger == YOK_KALEM:
            t["kalem_yok"], t["kalem_id"], t["kalem_ad"] = True, None, None
            valor = "İş kalemi yok"
        else:
            adaylar = katalog.kalemleri(t["faaliyet"], t["proje"])
            k = next((x for x in adaylar if x.kimlik == deger), None) if kaynak == "secim" else _entity_sec(adaylar, deger, "İş kalemi")
            if not k:
                raise ValueError("İş kalemi bu projede bulunamadı.")
            t["kalem_id"], t["kalem_ad"], t["kalem_yok"] = k.kimlik, k.ad, False
            valor = k.ad
    elif alan == "cari":
        if deger == YENI:
            raise ValueError("Yeni kişi/firma adını yazın.")
        adaylar = katalog.carileri(t["faaliyet"])
        c = next((x for x in adaylar if x.ad == deger), None) if kaynak == "secim" else None
        if c is None and kaynak != "secim":
            res = coz(deger, adaylar, bulanik=True)
            if res.durum == TEKIL:
                c = res.secilen
            elif res.durum in (BELIRSIZ, ONERI):
                raise ValueError("Birden fazla olası cari var: " + ", ".join(x.ad for x in res.adaylar[:5]) + ". Listeden seçin.")
            else:
                # tanimsiz ad: yeni cari onayi iste (uydurma yok, kullanici onaylar)
                if len(deger) < 2 or any(ch.isdigit() for ch in deger):
                    raise ValueError("Cari adını anlayamadım. Listeden seçin veya adı yazın.")
                t["bekleyen_yeni_cari"] = deger.strip()[:100]
                return f"Yeni cari adayı: {t['bekleyen_yeni_cari']}"
        if c is None:
            raise ValueError("Cari bu faaliyette bulunamadı.")
        t["cari"], t["cari_tipi"], t["cari_yeni"], t["bekleyen_yeni_cari"] = c.ad, c.ekle("cari_tipi"), False, None
        valor = c.ad
    elif alan == "cari_yeni_onay":
        if norm(deger) in ("evet", "e", "yes", "tamam"):
            t["cari"], t["cari_yeni"], t["cari_tipi"] = t["bekleyen_yeni_cari"], True, None
            valor = f"yeni cari: {t['cari']}"
            t["bekleyen_yeni_cari"] = None
        else:
            t["bekleyen_yeni_cari"] = None
            valor = "yeni cari reddedildi"
    elif alan == "cari_tipi":
        tipler = katalog.md.get("cari_tipler", [])
        tip = next((x for x in tipler if norm(x) == norm(deger)), None)
        if not tip:
            raise ValueError("Cari tipi tanımlı değil. Listeden seçin.")
        t["cari_tipi"] = valor = tip
    elif alan == "tutar":
        _tutar_uygula(t, deger)
        valor = tl_metin(t["tutar"], t["pb"] or "TL")
    elif alan == "tarih":
        sade = norm(deger)
        if sade in ("bugun", "dun"):
            tarih = date.today() if sade == "bugun" else date.fromordinal(date.today().toordinal() - 1)
            hata = ""
        else:
            tarih, _, hata = N.tarih_cikar(deger)
        if not tarih:
            raise ValueError(hata or "Tarihi anlayamadım. Örnek: 12.10.2026")
        t["tarih"], t["tarih_kaynagi"], t["tarih_hatasi"] = tarih.isoformat(), "secim", ""
        valor = tarih.strftime("%d.%m.%Y")
    elif alan == "hesap":
        adaylar = _hesap_adaylari(t, katalog)
        h = next((x for x in adaylar if x.ad == deger), None) if kaynak == "secim" else _entity_sec(adaylar, deger, "Hesap")
        if not h:
            raise ValueError("Hesap bu faaliyette/para biriminde bulunamadı.")
        t["hesap"], t["hesap_kaynagi"] = h.ad, "secim"
        valor = h.ad
    elif alan == "borc":
        valor = _borc_cevabi(t, deger, katalog, kaynak)
    elif alan == "mukerrer_onay":
        if norm(deger) not in ("evet", "yine de", "yine_de", "e", "tamam"):
            raise ValueError("Devam etmek için 'Yine de taslak hazırla'yı seçin ya da vazgeçin.")
        t["mukerrer_onay"] = True
        valor = "mükerrer uyarısına rağmen devam"
    elif alan == "kur":
        try:
            kur = Decimal(deger.replace(",", "."))
        except Exception:
            raise ValueError("Kuru sayı olarak yazın. Örnek: 41,25")
        if kur <= 0:
            raise ValueError("Kur sıfırdan büyük olmalı.")
        t["kur"] = str(kur)
        valor = str(kur)
    else:
        raise ValueError(f"Bilinmeyen alan: {alan}")
    t["belirsiz"].pop(alan, None)
    t["oneri"].pop(alan, None)
    return valor


def _borc_cevabi(t, deger, katalog, kaynak):
    """'borc' sorusunun cevabi: kapat:<cari> | yeni (serbest metinde evet/hayir/cari adi de anlasilir)."""
    s = _eslesme_hesapla(t, katalog)
    sade = norm(deger)
    secim = deger
    if kaynak != "secim":
        if sade in ("yeni", "yeni kayit", "hayir", "h", "hicbiri"):
            secim = "yeni"
        elif sade in ("evet", "e", "tamam", "kapat") and len(s["adaylar"]) == 1:
            secim = "kapat:" + s["adaylar"][0]["cari"]
        else:
            bulunan = [a for a in s["acik_kalemler"]
                       if norm(a["cari"]) and (norm(a["cari"]) in sade or sade in norm(a["cari"]))]
            if len(bulunan) == 1:
                secim = "kapat:" + bulunan[0]["cari"]
            else:
                raise ValueError("Cevabı anlayamadım. Listeden bir açık kaydı seçin ya da 'yeni kayıt' deyin.")
    if secim == "yeni":
        t["baglanti"], t["baglanti_karari"] = None, "yeni"
        return "yeni kayıt (mevcut borca/alacağa bağlanmadı)"
    if not secim.startswith("kapat:"):
        raise ValueError("Geçersiz seçim.")
    hedef = secim[len("kapat:"):]
    aday = next((a for a in s["adaylar"] if norm(a["cari"]) == norm(hedef)), None) \
        or next((a for a in s["acik_kalemler"] if norm(a["cari"]) == norm(hedef)), None)
    if aday is None:
        raise ValueError("Bu açık kayıt artık bulunamadı; lütfen yeniden seçin.")
    _baglanti_ata(t, aday, "kullanici_secimi" if kaynak == "secim" else "kullanici_metni", katalog)
    return "kapat: " + _baglanti_cumlesi(t["baglanti"])


def _islem_metinden(metin, katalog):
    sade = norm(metin)
    if "kalem" in sade:
        return N.WORK_ITEM_PAYMENT
    if "cari" in sade:
        return N.COUNTERPARTY_PAYMENT
    tip = N.islem_cikar(sade)
    if tip in (N.PAYMENT,):
        return N.COUNTERPARTY_PAYMENT
    if tip:
        return tip
    tur = next((x for x in katalog.islem_turleri if norm(x) == sade), None)
    return tur or ""


def _islem_tipi_turden(islem_turu):
    sade = norm(islem_turu)
    if "alacak duzeltme" in sade or "mahsup" in sade:
        return N.RECEIVABLE_ADJUSTMENT
    if "tahsilat" in sade:
        return N.COLLECTION
    if "hakedis" in sade:
        return N.RECEIVABLE
    if "gelir" in sade:
        return N.INCOME
    if "odeme" in sade:
        return N.COUNTERPARTY_PAYMENT
    return N.EXPENSE


# ---------------------------------------------------------------------------
# Taslak ozeti ve yanit
# ---------------------------------------------------------------------------

def ozet(t, katalog: Katalog) -> dict:
    f = _faaliyet(t, katalog)
    pb = _pb_coz(t, katalog)
    return {
        "faaliyet": f.gorunen() if f else None, "proje": t["proje"],
        "islem": N.ETIKET.get(t["islem"]) if t["islem"] and t["islem"] != N.PAYMENT else ("Ödeme" if t["islem"] else None),
        "islem_turu": _islem_turu(t, katalog), "kalem": t["kalem_ad"],
        "cari": t["cari"] + (" (yeni)" if t["cari_yeni"] else "") if t["cari"] else None,
        "tutar": tl_metin(t["tutar"], pb) if t["tutar"] else None, "pb": pb,
        "tarih": datetime.fromisoformat(t["tarih"]).strftime("%d.%m.%Y") if t["tarih"] else None,
        "tarih_notu": "Tarih belirtilmediği için bugün varsayıldı" if t["tarih_kaynagi"] == "varsayilan_bugun" else "",
        "hesap": t["hesap"], "hesap_notu": t["hesap_kaynagi"] if t["hesap_kaynagi"].startswith("otomatik") else "",
        "baglanti": (_baglanti_cumlesi(t["baglanti"]) if t.get("baglanti")
                     else ("yeni kayıt — mevcut borca/alacağa bağlanmadı" if t.get("baglanti_karari") == "yeni" else None)),
    }


def _ozet_satirlari(o: dict) -> list[str]:
    s = []
    for etiket, anahtar in (("Faaliyet", "faaliyet"), ("Proje", "proje"), ("İşlem", "islem"), ("İş kalemi", "kalem"),
                            ("Cari", "cari"), ("Tutar", "tutar"), ("Tarih", "tarih"), ("Hesap", "hesap"),
                            ("Bağlantı", "baglanti")):
        if o.get(anahtar):
            ek = ""
            if anahtar == "tarih" and o["tarih_notu"]:
                ek = f" ({o['tarih_notu']})"
            if anahtar == "hesap" and o["hesap_notu"]:
                ek = f" ({o['hesap_notu']})"
            s.append(f"{etiket}: {o[anahtar]}{ek}")
    return s


def _baglanti_satirlari(t) -> list[str]:
    """Kullaniciya acik: kayit yeni gider/gelir mi, yoksa mevcut borc/alacak kapama mi."""
    b = t.get("baglanti")
    if b:
        borc = b["tur"] == ES.BORC
        if t["islem"] == N.RECEIVABLE_ADJUSTMENT:
            ilk, ikinci = "Bu kayıt yeni gelir değil; mevcut alacağın silinmesi (düzeltme) olarak işlendi.", "Bu işlem şu açık alacağı siliyor: "
        elif borc:
            ilk, ikinci = "Bu kayıt yeni gider değil; mevcut borç kapama olarak işlendi.", "Bu ödeme şu açık borcu kapatıyor: "
        else:
            ilk, ikinci = "Bu kayıt yeni gelir değil; mevcut alacak kapama olarak işlendi.", "Bu tahsilat şu açık alacağı kapatıyor: "
        satirlar = [ilk, ikinci + f"{b['cari']} / {b['proje']} / kalan {_kurus_metin(b['kalan_kurus'])} "
                    f"→ {_kurus_metin(b['kalan_sonra_kurus'])}"]
        if b.get("asiyor"):
            satirlar.append("Dikkat: tutar kalan bakiyeyi aşıyor; fazlası avans/fazla tahsilat olarak kalır.")
        return satirlar
    if t.get("baglanti_karari") == "yeni":
        return ["Bu kayıt mevcut bir borca/alacağa bağlanmadı; yeni kayıt olarak hazırlandı."]
    return []


_EKSIK_ETIKETLERI = {
    "faaliyet": "Faaliyet adı?", "proje": "Proje adı?", "islem": "İşlem türü (ödeme, gider, tahsilat…)?",
    "kalem": "Hangi iş kalemine bağlanacak?", "cari": "Cari (kime/kimden)?", "cari_yeni_onay": "Yeni cari olarak eklensin mi?",
    "cari_tipi": "Cari tipi?", "tutar": "Tutar ve para birimi?", "tarih": "İşlem tarihi?",
    "hesap": "Para hangi kasa/banka hesabından çıktı veya nereye girdi?", "kur": "Kur?",
    "borc": "Bu yeni kayıt mı, mevcut borç/alacak kapama mı?",
    "mukerrer_onay": "Aynı kayıt zaten var; yine de taslak hazırlansın mı?",
}


def _eksik_listesi(t, katalog) -> list[str]:
    try:
        return [_EKSIK_ETIKETLERI.get(a, a) for a in eksik_alanlar(t, katalog)]
    except Exception:
        return []


def _yanit(t, katalog, soru=None, kayit_taslagi=None, hata=None, uyarilar=None) -> dict:
    o = ozet(t, katalog)
    satirlar = _ozet_satirlari(o)
    cevap = []
    if hata:
        cevap.append(f"⚠ {hata}")
    if kayit_taslagi:
        cevap.append("Kayıt taslağını hazırladım:")
        cevap.extend(satirlar)
        cevap.extend(_baglanti_satirlari(t))
        cevap.extend(f"• {u}" for u in (uyarilar or []))
        cevap.append("Doğruysa 'Şifreyle Onayla'ya basın. Onaydan önce Excel'e hiçbir şey yazılmadı.")
    elif soru:
        if satirlar:
            cevap.append("Şimdiye kadar anladıklarım:")
            cevap.extend(satirlar)
        eksikler = _eksik_listesi(t, katalog)
        if eksikler:
            cevap.append("Bu kaydı yazmadan önce şu bilgi eksik:")
            cevap.extend(f"{i}. {x}" for i, x in enumerate(eksikler, 1))
        cevap.append(soru["metin"])
    return {
        "cevap": "\n".join(cevap), "niyet": "cfo_sihirbaz", "guven": "Yüksek", "llm": "deterministik",
        "kanitlar": [], "aksiyonlar": [], "degisiklik_yapildi": False,
        "cfo": {"taslak_id": t["id"], "durum": t["durum"], "alanlar": o, "soru": soru, "notlar": list(t["notlar"]),
                "belge_id": t.get("belge_id")},
        "kayit_taslagi": kayit_taslagi, "hata": bool(hata),
    }


def _aday_filtresi(t, alan, soru):
    """Belirsiz/oneri adaylari varsa secenekleri yalniz onlarla sinirlar ('Devrim mi, Devrim Yildiz mi?')."""
    adaylar = t["belirsiz"].get(alan) or t["oneri"].get(alan)
    if not adaylar or alan not in ("proje", "kalem", "cari", "hesap"):
        return soru
    adlar = {a["ad"] for a in adaylar}
    kimlikler = {a["kimlik"] for a in adaylar}
    sinirli = [x for x in soru["secenekler"] if x["id"] in adlar or x["id"] in kimlikler]
    if sinirli:
        soru = dict(soru, secenekler=sinirli,
                    metin=("Birden fazla olası eşleşme var. " if t["belirsiz"].get(alan) else "Şunu mu demek istediniz? ") + soru["metin"])
    return soru


def _siradaki_soru(t, katalog):
    for alan in eksik_alanlar(t, katalog):
        t["bekleyen_alan"] = alan
        return _aday_filtresi(t, alan, soru_uret(t, alan, katalog))
    t["bekleyen_alan"] = None
    return None


# ---------------------------------------------------------------------------
# Kayit uretimi + mevcut onay kuyruguna aktarim
# ---------------------------------------------------------------------------

def _aciklama(t, katalog):
    if t.get("kaynak_metin") and len(norm(t["kaynak_metin"]).split()) >= 4 and t["tutar"]:
        return t["kaynak_metin"].strip()[:500]
    o = ozet(t, katalog)
    return " | ".join(_ozet_satirlari({**o, "tarih": None, "hesap": None, "baglanti": None}))[:500]


def kayit_olustur(t, katalog: Katalog) -> dict:
    f = _faaliyet(t, katalog)
    pb = _pb_coz(t, katalog)
    kur = _kur(t, katalog)
    kayit = {
        "Tarih": t["tarih"], "Faaliyet": f.ad, "Proje": t["proje"],
        "Cari_Tipi": t["cari_tipi"] or ("Diğer" if t["cari_yeni"] else ""), "Cari": t["cari"],
        "Islem_Turu": _islem_turu(t, katalog), "Hesap_Kasa": t["hesap"] or "",
        "Tutar": float(Decimal(t["tutar"])), "PB": pb, "Kur": float(kur) if kur else "",
        "Vade": "", "Aciklama": _aciklama(t, katalog),
    }
    if t["cari_yeni"]:
        kayit["_yeni_cari"] = True
    if t["kalem_id"]:
        kayit["Baglanti_ID"] = t["kalem_id"]       # 03_Kayit Merkezi 'Baglanti_ID' sutunu is kalemini baglar
        kayit["Is_Kalemi_ID"] = t["kalem_id"]
        kayit["Aciklama"] = f"[{t['kalem_ad']}] " + kayit["Aciklama"]
        kayit["Aciklama"] = kayit["Aciklama"][:500]
    if t.get("baglanti"):
        b = t["baglanti"]
        # Odemeyi alan kisi ``Cari`` olarak KALIR; kapattigi acik borc/alacak defterde kalici etiketle baglanir.
        kayit["Aciklama"] = (pf.baglanti_etiketi(b["tur"], b["cari"]) + kayit["Aciklama"])[:500]
        kayit["_baglanti"] = {k: b.get(k) for k in ("tur", "cari", "proje", "kalan_kurus", "kalan_sonra_kurus",
                                                    "skor", "kaynak")}
    otomatik = []
    if t["hesap_kaynagi"].startswith("otomatik"):
        otomatik.append("Hesap " + t["hesap_kaynagi"][len("otomatik: "):] + f": {t['hesap']}")
    otomatik += [x for x in t["notlar"] if x.startswith(("Faaliyet projeden", "Proje ", "Açık kalem", "Kapatılacak açık"))]
    if otomatik:
        kayit["_otomatik_eslesmeler"] = otomatik
    return kayit


def _benzer_kayit_uyarisi(t, kayit, katalog):
    """Ayni gun/cari/proje/tutarli kayit zaten defterde varsa uyari metni (taslak onay asamasinda gosterilir)."""
    fisler = _benzer_fisler(t, katalog)
    return [f"Dikkat: aynı gün, aynı cari ve aynı tutarla kayıt zaten var ({fisler[0]}). "
            "Yinelenen bir giriş değilse onaylayın."] if fisler else []


def hazirla(t, katalog: Katalog) -> dict:
    """Tum alanlar tamam: dogrular ve MEVCUT onay kuyruguna koyar. Excel'e yazmaz."""
    import ui_belge_servisi as ui

    firma_key = t["faaliyet"]
    md = master_data_oku(firma_key)
    kayit = kayit_olustur(t, katalog)
    veri: dict[str, Any] = {"_hedef": "ana_ledger", "kayitlar": [kayit], "uyarilar": [], "riskler": []}
    dogrulama = deterministik_dogrula(veri, firma_key, "sohbet_kaydi", md=md)
    if dogrulama["durum"] == FAIL:
        return {"hata": "; ".join(dogrulama["hatalar"][:3])}
    kayit = veri["kayitlar"][0]
    benzer = _benzer_kayit_uyarisi(t, kayit, katalog)
    dogrulama["uyarilar"].extend(benzer)
    normalize = "|".join([t["islem"] or "", firma_key, norm(t["proje"]), norm(t["cari"]), norm(t["kalem_id"]),
                          t["tutar"], _pb_coz(t, katalog), t["tarih"], norm(t["hesap"])]
                         + (["kapat:" + norm(t["baglanti"]["cari"])] if t.get("baglanti") else []))
    belge_id = f"{firma_key[:4].upper()}-CHAT-{t['id']}"
    belge_hash = hashlib.sha256(f"{normalize}|{t['tekrar_no']}".encode("utf-8")).hexdigest()
    for k in veri["kayitlar"]:
        k["_dosya_adi"] = "CFO asistanı sohbet kaydı"
        k["_guven"] = dogrulama["skor"] / 100.0
        k["_hash"] = belge_hash
        k["_dogrulama_durumu"] = dogrulama["durum"]
        k["_dogrulama_ozeti"] = "; ".join((dogrulama["hatalar"] + dogrulama["uyarilar"])[:3])
        from guvenlik import finansal_fingerprint

        k["_fingerprint"] = finansal_fingerprint(k, firma_key, "sohbet_kaydi")
    hazir = {
        "basari": True, "foto_yolu": None, "model_yolu": None, "kaynak_metin": "",
        "orijinal_talimat": t["kaynak_metin"], "normalize_komut": normalize, "taslak_id": t["id"],
        "firma_key": firma_key, "firma_guven": 100, "tur": "sohbet_kaydi", "tur_guven": dogrulama["skor"],
        "veri": veri, "belge_id": belge_id, "belge_hash": belge_hash, "dogrulama": dogrulama,
        "gecici_dosyalar": [], "cikarim_motoru": MOTOR,
    }
    audit_yaz("cfo_taslak_hazir", taslak_id=t["id"], belge_id=belge_id, orijinal_talimat=t["kaynak_metin"],
              normalize_komut=normalize, faaliyet=firma_key, proje=t["proje"], cari=t["cari"],
              islem=t["islem"], tutar=t["tutar"], pb=_pb_coz(t, katalog), tarih=t["tarih"], aktor=t["aktor"],
              durum=dogrulama["durum"], skor=dogrulama["skor"],
              baglanti_cari=(t.get("baglanti") or {}).get("cari"), baglanti_karari=t.get("baglanti_karari"),
              baglanti_kaynagi=(t.get("baglanti") or {}).get("kaynak"))
    kuyruk = ui._kuyruga_al(hazir, t["aktor"], "sohbet")
    t["durum"], t["onay_token"], t["belge_id"] = ONAY_BEKLIYOR, kuyruk["token"], belge_id
    return {"kuyruk": kuyruk, "uyarilar": dogrulama["uyarilar"][:4]}


def _ilerlet(t, katalog, hata=None) -> dict:
    """Sonraki eksik alani sorar veya tamamsa onay kuyruguna koyar."""
    soru = _siradaki_soru(t, katalog)
    if soru is None:
        sonuc = hazirla(t, katalog)
        if "hata" in sonuc:
            t["durum"] = TOPLANIYOR
            return _yanit(t, katalog, hata=f"Taslak doğrulanamadı: {sonuc['hata']}. Henüz hiçbir kayıt oluşturulmadı.")
        return _yanit(t, katalog, kayit_taslagi=sonuc["kuyruk"], uyarilar=sonuc["uyarilar"])
    return _yanit(t, katalog, soru=soru, hata=hata)


# ---------------------------------------------------------------------------
# Disari acik API
# ---------------------------------------------------------------------------

def baslat(niyet: N.Niyet, aktor, katalog: Katalog | None = None, yine_de=False, bugun=None, kapsam=None) -> dict:
    """Cikarilan niyetle yeni taslak baslatir. Ayni talimat bugun zaten isleme alinmissa uyarir."""
    katalog = katalog or katalog_yukle()
    with _LOCK:
        anahtar = komut_anahtari(niyet.metin, aktor, bugun)
        mevcut = [x for x in _TASLAKLAR.values() if x["komut_anahtari"] == anahtar and x["durum"] != IPTAL]
        acik = next((x for x in mevcut if x["durum"] in (TOPLANIYOR, ONAY_BEKLIYOR)), None)
        if acik and not yine_de:
            audit_yaz("cfo_taslak_tekrar_acik", taslak_id=acik["id"], aktor=str(aktor))
            yanit = _devam_yaniti(acik, katalog)
            yanit["cevap"] = "Bu talimat için zaten açık bir taslak var; yeni taslak oluşturmadım.\n" + yanit["cevap"]
            return yanit
        yapilmis = [x for x in mevcut if x["durum"] == TAMAMLANDI]
        # Tekrar korumasi yalniz TUTAR iceren talimatlar icindir; "yeni odeme" gibi bos baslangic finansal komut degildir.
        if yapilmis and not yine_de and niyet.tutar:
            son = yapilmis[-1]
            audit_yaz("cfo_taslak_tekrar_engellendi", taslak_id=son["id"], belge_id=son.get("belge_id"),
                      orijinal_talimat=niyet.metin, aktor=str(aktor))
            return {
                "cevap": ("Bu talimat bugün zaten işlendi" + (f" (kayıt: {son.get('belge_id')})" if son.get("belge_id") else "")
                          + ". Yanlışlıkla ikinci kez yazılmasın diye yeni kayıt oluşturmadım. "
                          "Gerçekten ikinci bir kayıt gerekiyorsa 'Yine de yeni kayıt olarak ekle'ye basın."),
                "niyet": "cfo_tekrar_uyarisi", "guven": "Yüksek", "llm": "deterministik", "kanitlar": [],
                "aksiyonlar": [], "degisiklik_yapildi": False,
                "cfo": {"taslak_id": None, "durum": "TEKRAR", "alanlar": {}, "notlar": [], "tekrar_metni": niyet.metin,
                        "soru": {"alan": "tekrar", "metin": "Yine de yeni bir kayıt oluşturulsun mu?",
                                 "secenekler": [_secenek("yine_de", "Yine de yeni kayıt olarak ekle"),
                                                _secenek("vazgec", "Vazgeç")]}},
                "kayit_taslagi": None,
            }
        t = _bos_taslak(niyet.metin, aktor, tekrar_no=len(yapilmis) + len(mevcut) if yine_de else 0, bugun=bugun)
        t["kapsam"] = list(kapsam) if kapsam else None
        t["yine_de"] = bool(yine_de)                  # kullanici 'Yine de ekle' dedi: mukerrer sorulmaz
        _niyetten_uygula(t, niyet, katalog)
        if "tarih_hatasi" in t and t["tarih_hatasi"]:
            t["tarih"] = None
        _TASLAKLAR[t["id"]] = t
        audit_yaz("cfo_taslak_olustu", taslak_id=t["id"], orijinal_talimat=niyet.metin, aktor=str(aktor),
                  islem=t["islem"], faaliyet=t["faaliyet"], proje=t["proje"], cari=t["cari"], tutar=t["tutar"],
                  notlar=t["notlar"], tekrar_no=t["tekrar_no"])
        yanit = _ilerlet(t, katalog)
        t["guncelleme"] = _simdi()
        _kaydet()
        return yanit


def _devam_yaniti(t, katalog):
    if t["durum"] == ONAY_BEKLIYOR:
        import ui_belge_servisi as ui

        try:
            return _yanit(t, katalog, kayit_taslagi=ui.ozet(t["onay_token"]))
        except ValueError:
            t["durum"] = TOPLANIYOR
    return _ilerlet(t, katalog)


def devam(taslak_id, katalog: Katalog | None = None) -> dict:
    katalog = katalog or katalog_yukle()
    with _LOCK:
        t = _kayitli_getir(taslak_id)
        return _devam_yaniti(t, katalog)


def cevapla(taslak_id, alan, deger, aktor, kaynak="secim", katalog: Katalog | None = None) -> dict:
    """Bir sorunun cevabini (secenek tiklamasi veya yazili metin) isler; LLM kullanmaz."""
    katalog = katalog or katalog_yukle()
    with _LOCK:
        t = _kayitli_getir(taslak_id)
        if t["aktor"] != str(aktor):
            raise PermissionError("Bu taslak başka bir kullanıcıya ait.")
        if t["durum"] not in (TOPLANIYOR,):
            raise ValueError("Taslak artık düzenlenemez (onay bekliyor veya tamamlandı).")
        beklenen = t.get("bekleyen_alan")
        alan = alan or beklenen
        if not alan:
            raise ValueError("Cevaplanacak açık bir soru yok.")
        if alan == "mukerrer_onay" and norm(str(deger)) in ("hayir", "vazgec", "iptal", "h"):
            t["durum"], t["guncelleme"] = IPTAL, _simdi()
            _adim_kaydet(t, alan, "mükerrer olabileceği için vazgeçildi", kaynak)
            _kaydet()
            audit_yaz("cfo_taslak_iptal", taslak_id=t["id"], onceki_durum=TOPLANIYOR, neden="mukerrer", aktor=str(aktor))
            return {"cevap": "Mükerrer olabileceği için vazgeçildi; kayıt taslağı oluşturulmadı. "
                             "Excel'e hiçbir kayıt yazılmadı.",
                    "niyet": "kayit_reddi", "taslak_kapat": True, "degisiklik_yapildi": False,
                    "cfo": {"taslak_id": t["id"], "durum": IPTAL}, "kanitlar": [], "aksiyonlar": [],
                    "guven": "Yüksek", "llm": "deterministik"}
        try:
            gosterim = _uygula(t, alan, deger, katalog, kaynak)
        except ValueError as exc:
            return _ilerlet(t, katalog, hata=str(exc))
        _adim_kaydet(t, alan, gosterim, kaynak)
        t["guncelleme"] = _simdi()
        yanit = _ilerlet(t, katalog)
        _kaydet()
        return yanit


def metinle_cevapla(taslak_id, metin, aktor, katalog: Katalog | None = None) -> dict:
    """Serbest metni, sihirbazin sorduğu alanin cevabi olarak isler.

    Metin tek basina tam yeni bir talimat gibiyse (tutar + islem) cagiran taraf yeni taslak baslatir;
    burada yalnizca beklenen alan cozulur. Ek olarak ayni mesajdaki baska alanlar (ornegin
    'Gursar, 25 bin') da yakalanir.
    """
    katalog = katalog or katalog_yukle()
    with _LOCK:
        t = _kayitli_getir(taslak_id)
        alan = t.get("bekleyen_alan")
    if not alan:
        return cevapla(taslak_id, None, metin, aktor, "metin", katalog)
    return cevapla(taslak_id, alan, metin, aktor, "metin", katalog)


def iptal(taslak_id, aktor):
    with _LOCK:
        t = _kayitli_getir(taslak_id)
        if t["aktor"] != str(aktor):
            raise PermissionError("Bu taslak başka bir kullanıcıya ait.")
        onceki = t["durum"]
        t["durum"], t["guncelleme"] = IPTAL, _simdi()
        _kaydet()
    audit_yaz("cfo_taslak_iptal", taslak_id=taslak_id, onceki_durum=onceki, aktor=str(aktor))
    return {"cevap": "Taslak iptal edildi. Excel'e hiçbir kayıt yazılmadı.", "niyet": "kayit_reddi",
            "taslak_kapat": True, "degisiklik_yapildi": False, "cfo": {"taslak_id": taslak_id, "durum": IPTAL},
            "kanitlar": [], "aksiyonlar": [], "guven": "Yüksek", "llm": "deterministik"}


def onay_tamamlandi(onay_token):
    """Onay kuyrugunda kayit yazildiginda taslagi TAMAMLANDI yapar (idempotency icin kayit)."""
    with _LOCK:
        for t in _TASLAKLAR.values():
            if t.get("onay_token") == onay_token and t["durum"] == ONAY_BEKLIYOR:
                t["durum"], t["guncelleme"] = TAMAMLANDI, _simdi()
                _kaydet()
                return t["id"]
    return None


def reddedildi(onay_token):
    with _LOCK:
        for t in _TASLAKLAR.values():
            if t.get("onay_token") == onay_token and t["durum"] == ONAY_BEKLIYOR:
                t["durum"], t["guncelleme"] = IPTAL, _simdi()
                _kaydet()
                return t["id"]
    return None


def duzelt(taslak_id, metin, aktor, katalog: Katalog | None = None) -> dict:
    """Onay bekleyen taslagi deterministik olarak duzeltir ('Duzelt: tutar 12 bin, hesap ...')."""
    import ui_belge_servisi as ui

    katalog = katalog or katalog_yukle()
    with _LOCK:
        t = _kayitli_getir(taslak_id)
        if t["aktor"] != str(aktor):
            raise PermissionError("Bu taslak başka bir kullanıcıya ait.")
        if t["durum"] not in (TOPLANIYOR, ONAY_BEKLIYOR):
            raise ValueError("Taslak düzeltmeye açık değil.")
        govde = metin.split(":", 1)[-1] if norm(metin).startswith("duzelt") else metin
        n = N.analiz_et(govde, katalog, zorla=True)
        degisti = []
        if n.tutar:
            t["tutar"] = str(n.tutar)
            if n.pb:
                t["pb"], t["pb_acik"] = n.pb, True
            degisti.append("tutar")
        if n.tarih and n.tarih_kaynagi == "metin":
            t["tarih"], t["tarih_kaynagi"], t["tarih_hatasi"] = n.tarih.isoformat(), "duzeltme", ""
            degisti.append("tarih")
        if n.proje:
            t["proje"], t["kalem_id"], t["kalem_ad"] = n.proje.ad, None, None
            degisti.append("proje")
        if n.kalem:
            t["kalem_id"], t["kalem_ad"], t["kalem_yok"] = n.kalem.kimlik, n.kalem.ad, False
            degisti.append("iş kalemi")
        if n.cari:
            t["cari"], t["cari_tipi"], t["cari_yeni"] = n.cari.ad, n.cari.ekle("cari_tipi"), False
            degisti.append("cari")
        if n.hesap:
            t["hesap"], t["hesap_kaynagi"] = n.hesap.ad, "duzeltme"
            degisti.append("hesap")
        if not degisti:
            return {"cevap": "Düzeltmeyi anlayamadım. Örnek: ‘Düzelt: tutar 12 bin TL’, ‘Düzelt: hesap Gürsar TL Kasa’, "
                             "‘Düzelt: tarih 10.10.2026’. Taslak değişmedi.",
                    "niyet": "kayit_eksikleri_bekliyor", "guven": "Yüksek", "llm": "deterministik",
                    "degisiklik_yapildi": False, "kanitlar": [], "aksiyonlar": [],
                    "cfo": {"taslak_id": t["id"], "durum": t["durum"]}}
        if set(degisti) & {"tutar", "proje", "iş kalemi", "cari"}:
            _baglanti_sifirla(t)                      # baglanti/mukerrer onayi yeni bilgiyle yeniden degerlendirilir
        if t["durum"] == ONAY_BEKLIYOR and t["onay_token"]:
            try:
                ui.reddet(t["onay_token"], aktor)       # eski kuyruk girdisi kapanir; yeni onay yeni sifre ister
            except ValueError:
                pass
        t["durum"], t["onay_token"] = TOPLANIYOR, None
        t["tekrar_no"] += 1
        _adim_kaydet(t, "duzeltme", ", ".join(degisti), "metin")
        yanit = _ilerlet(t, katalog)
        _kaydet()
        yanit["cevap"] = f"Düzeltildi ({', '.join(degisti)}).\n" + yanit["cevap"]
        return yanit


def aktif_taslak(aktor):
    """Kullanicinin son acik (toplaniyor/onay bekliyor) taslagi — istemci durumunu yitirirse geri yukleme."""
    with _LOCK:
        acik = [x for x in _TASLAKLAR.values() if x["aktor"] == str(aktor) and x["durum"] in (TOPLANIYOR, ONAY_BEKLIYOR)]
        return max(acik, key=lambda x: x["guncelleme"]) if acik else None
