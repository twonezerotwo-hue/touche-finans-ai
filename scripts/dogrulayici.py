# -*- coding: utf-8 -*-
"""Qwen ciktisini deterministik kurallarla normalize eder ve dogrular."""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from master_data import master_data_oku
from config import FIRMALAR

log = logging.getLogger(__name__)

PASS = "PASS"
REVIEW = "REVIEW"
FAIL = "FAIL"

BEKLENEN_ALANLAR = {
    "touche_holding": {
        "sohbet_kaydi": ["Tarih", "Faaliyet", "Cari_Tipi", "Cari", "Islem_Turu", "Tutar", "PB", "Kur"],
        "irsaliye": ["Tarih", "Irsaliye_No", "Tedarikci", "Urun", "Miktar", "Birim"],
        "fatura": ["Tarih", "Fatura_No", "Tedarikci", "Urun", "Miktar", "Birim_Fiyat", "Toplam"],
        "banka_dekontu": ["Tarih", "Gonderen", "Alici", "Tutar"],
        "hakedis": ["Donem", "Hakedis_No", "Is_Kalemi", "Metraj", "Birim_Fiyat", "Tutar"],
        "cek": ["Cek_No", "Banka", "Kesideci", "Vade_Tarihi", "Tutar"],
        "sozlesme": ["Sozlesme_No", "Taraflar", "Konu"],
        "makbuz": ["Tarih", "Makbuz_No", "Odeyen", "Tutar"],
        "tutanak": ["Tarih", "Konu"],
        "puantaj": ["Donem", "Personel"],
        "police": ["Police_No", "Sigorta_Sirketi", "Sigortali"],
        "izin_ruhsat": ["Belge_No", "Tur", "Sahibi"],
        "teminat_mektubu": ["Mektup_No", "Banka", "Tutar"],
        "teklif": ["Tarih", "Teklif_No", "Firma", "Urun"],
        "sevk_irsaliye": ["Tarih", "Sevk_No", "Tedarikci", "Alici"],
    },
    "gursar_mimarlik": {
        "sohbet_kaydi": ["Tarih", "Faaliyet", "Proje", "Cari_Tipi", "Cari", "Islem_Turu", "Tutar", "PB", "Kur"],
        "odeme_dekontu": ["Tarih", "Cari", "Islem_Turu", "Tutar"],
    },
}

_ANAHTAR_ALIASLARI = {
    "Cari": ("Cari", "Alici", "Alıcı", "Tedarikci", "Tedarikçi", "Firma"),
    "Tutar": ("Tutar", "Tutar_TL", "Toplam", "Prim"),
    "Islem_Turu": ("Islem_Turu", "İşlem_Türü", "IslemTuru", "Tur"),
}

_TOUCHE_ISLEM = {
    "fatura": "Gider Tahakkuku",
    "irsaliye": "Gider Tahakkuku",
    "hakedis": "Hakediş / Gelir Tahakkuku",
    "cek": "Çek Alındı",
    "makbuz": "Tahsilat",
    "teminat_mektubu": "Teminat Mektubu",
}


def _norm(metin) -> str:
    metin = unicodedata.normalize("NFKD", str(metin or ""))
    metin = "".join(ch for ch in metin if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", metin.casefold().replace("ı", "i"))


def _normalize_kayit(kayit, beklenen_alanlar):
    """Anahtar bicimi ve bilinen alias farklarini giderir; ek alanlari korur."""
    if not isinstance(kayit, dict):
        return kayit
    bulunan = {_norm(k): (k, v) for k, v in kayit.items()}
    yeni = dict(kayit)
    for alan in beklenen_alanlar:
        adaylar = _ANAHTAR_ALIASLARI.get(alan, (alan,))
        for aday in adaylar:
            eslesme = bulunan.get(_norm(aday))
            if eslesme:
                yeni[alan] = eslesme[1]
                break
        else:
            yeni.setdefault(alan, "")
    return yeni


def _kanonik_alanlari_tamamla(kayit, firma_key, tur):
    """Excel katmaninin ortak alanlarini yalnizca acik veriden turetir."""
    firma = FIRMALAR.get(firma_key, {})
    if not kayit.get("PB"):
        kayit["PB"] = _para_birimi_bul(kayit.get("Tutar")) or "TL"
    if firma.get('adapter') == 'standart_v1':
        kayit['Faaliyet'] = kayit.get('Faaliyet') or firma['faaliyet']
        kayit['PB'] = kayit.get('PB') or firma['pb']
        kayit['Hesap_Kasa'] = kayit.get('Hesap_Kasa') or firma['varsayilan_hesap']
        if kayit['PB'] == 'TL' and not kayit.get('Kur'):
            kayit['Kur'] = 1
        return
    if firma_key == "gursar_mimarlik":
        kayit["Faaliyet"] = kayit.get("Faaliyet") or "Gürsar İnşaat"
        kayit["PB"] = kayit.get("PB") or "TL"
        kayit["Cari_Tipi"] = kayit.get("Cari_Tipi") or _gursar_cari_tipi(kayit.get("Islem_Turu"))
        # Belge adaptörlerinde eski varsayılanı koru. Sohbet kaydında ise
        # banka/kasa ayrımı finansal sonucu değiştirdiğinden tahmin etme;
        # açık metinden veya güvenli benzer kayıt eşleşmesinden gelmelidir.
        if tur != "sohbet_kaydi":
            kayit["Hesap_Kasa"] = kayit.get("Hesap_Kasa") or "Gürsar TL Banka"
        return

    kayit["PB"] = kayit.get("PB") or "TL"
    if not kayit.get("Cari"):
        for alan in ("Tedarikci", "Firma", "Alici", "Alan", "Kesideci", "Sigortali", "Sahibi"):
            if kayit.get(alan):
                kayit["Cari"] = kayit[alan]
                break
    if not kayit.get("Tutar"):
        for alan in ("Toplam", "Prim"):
            if kayit.get(alan) not in (None, ""):
                kayit["Tutar"] = kayit[alan]
                break
    if not kayit.get("Tarih"):
        for alan in ("Onay_Tarihi", "Imza_Tarihi", "Vade_Tarihi", "Baslangic"):
            if kayit.get(alan):
                kayit["Tarih"] = kayit[alan]
                break
    kayit.setdefault("Islem_Turu", _TOUCHE_ISLEM.get(tur, ""))
    if not kayit.get("Cari_Tipi"):
        if kayit.get("Tedarikci") or tur in {"fatura", "irsaliye", "teklif", "sevk_irsaliye"}:
            kayit["Cari_Tipi"] = "Tedarikçi"


def _gursar_cari_tipi(islem_turu):
    return {
        "Usta ödemesi": "Usta",
        "Ortak cari ödemesi": "Ortak",
        "Banka masrafı": "Banka",
        "Diğer": "Diğer",
    }.get(str(islem_turu or "").strip(), "")


def _para_birimi_bul(deger):
    """Tutar metnindeki acik para birimini kanonik koda cevirir."""
    metin = str(deger or "").upper()
    if "USDT" in metin:
        return "USDT"
    if "USD" in metin or "$" in metin:
        return "USD"
    if "EUR" in metin or "€" in metin:
        return "EUR"
    if "TRY" in metin or " TL" in f" {metin}" or "₺" in metin:
        return "TL"
    return ""


def _tutar_cevir(deger):
    """Turkce/uluslararasi yazilmis para tutarini Decimal'e cevirir.

    Ornekler: ``67.000 TL``, ``2.275,50 ₺``, ``2,275.50 USD``,
    ``300 bin TL`` ve ``2,5 milyon``.
    """
    if deger in (None, ""):
        return None
    if isinstance(deger, Decimal):
        return deger
    metin = str(deger).strip().casefold().replace("−", "-").replace("–", "-")
    carpan = Decimal("1")
    if re.search(r"\bmilyar\b", metin):
        carpan = Decimal("1000000000")
    elif re.search(r"\bmilyon\b", metin):
        carpan = Decimal("1000000")
    elif re.search(r"\bbin\b", metin) or re.search(r"(?<=\d)\s*k\b", metin):
        carpan = Decimal("1000")
    metin = re.sub(r"\b(?:try|tl|usd|usdt|eur|turk\s*lirasi|türk\s*lirası|lira)\b", "", metin)
    metin = re.sub(r"\b(?:bin|milyon|milyar)\b", "", metin)
    metin = metin.replace("₺", "").replace("$", "").replace("€", "")
    metin = re.sub(r"(?<=\d)\s*k\b", "", metin)
    metin = re.sub(r"[^0-9,\.\-+]", "", metin)
    if not metin or metin in {"-", "+"}:
        return None

    if "," in metin and "." in metin:
        # Son ayirac ondalik, digerleri binlik kabul edilir.
        ondalik = "," if metin.rfind(",") > metin.rfind(".") else "."
        binlik = "." if ondalik == "," else ","
        metin = metin.replace(binlik, "")
        if ondalik != ".":
            metin = metin.replace(ondalik, ".")
    elif "," in metin or "." in metin:
        ayirac = "," if "," in metin else "."
        parcalar = metin.split(ayirac)
        if len(parcalar) > 2:
            if all(len(x) == 3 for x in parcalar[1:]):
                metin = "".join(parcalar)
            else:
                metin = "".join(parcalar[:-1]) + "." + parcalar[-1]
        else:
            tam, kesir = parcalar
            # 67.000 / 1,250 gibi yaygin binlik yazimlarini koru;
            # 0,125 ve 48,4305 gibi degerleri ondalik say.
            if len(kesir) == 3 and tam.lstrip("+-") not in {"", "0"} and len(tam.lstrip("+-")) <= 3:
                metin = tam + kesir
            else:
                metin = tam + "." + kesir
    try:
        return Decimal(metin) * carpan
    except (InvalidOperation, ValueError):
        return None


def _tarih_gecerli(s):
    if not s:
        return False
    if isinstance(s, datetime):
        return True
    metin = str(s).strip()
    for bicim in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            datetime.strptime(metin, bicim)
            return True
        except ValueError:
            pass
    return False


def _listede(deger, liste):
    aranan = _norm(deger)
    return any(_norm(aday) == aranan for aday in liste)


def _master_data_kontrol(kayit, md, firma_key, sira):
    uyarilar = []
    yeni_cari = bool(kayit.get("_yeni_cari"))
    faaliyet = kayit.get("Faaliyet")
    proje = kayit.get("Proje")
    cari_tipi = kayit.get("Cari_Tipi")
    cari = kayit.get("Cari") or kayit.get("Alici")
    islem = kayit.get("Islem_Turu")

    if faaliyet and md["faaliyetler"] and not _listede(faaliyet, md["faaliyetler"]):
        uyarilar.append(f"Kayit {sira}: faaliyet master data'da yok -> {faaliyet}")
    if proje and md["projeler"] and not _listede(proje, md["projeler"]):
        uyarilar.append(f"Kayit {sira}: proje master data'da yok -> {proje}")
    if faaliyet and proje and md.get("proje_hiyerarsisi"):
        uygun = any(_norm(x["faaliyet"]) == _norm(faaliyet) and _norm(x["proje"]) == _norm(proje)
                    for x in md["proje_hiyerarsisi"])
        if not uygun:
            uyarilar.append(f"Kayit {sira}: faaliyet/proje hiyerarsisi gecersiz -> {faaliyet} / {proje}")
    if (cari_tipi and md["cari_tipler"] and not _listede(cari_tipi, md["cari_tipler"])
            and not (yeni_cari and _norm(cari_tipi) == "diger")):
        uyarilar.append(f"Kayit {sira}: cari tipi master data'da yok -> {cari_tipi}")
    if cari and md["cariler"] and not _listede(cari, md["cariler"]) and not yeni_cari:
        uyarilar.append(f"Kayit {sira}: cari master data'da yok -> {cari}")
    if islem and md["islem_turleri"] and not _listede(islem, md["islem_turleri"]):
        uyarilar.append(f"Kayit {sira}: islem turu master data'da yok -> {islem}")
    if firma_key == "gursar_mimarlik" and kayit.get("Maliyet_Grubu"):
        if not _listede(kayit["Maliyet_Grubu"], md["maliyet_gruplari"]):
            uyarilar.append(f"Kayit {sira}: maliyet grubu gecersiz -> {kayit['Maliyet_Grubu']}")
    return uyarilar


def deterministik_dogrula(veri, firma_key, tur, md=None):
    """Normalize edilmis veri ile PASS/REVIEW/FAIL sonucu dondurur."""
    hatalar = []
    uyarilar = []
    if not isinstance(veri, dict):
        return {"durum": FAIL, "skor": 0, "hatalar": ["Veri sozluk degil"], "uyarilar": [], "veri": veri}
    if not isinstance(veri.get("kayitlar"), list) or not veri.get("kayitlar"):
        return {"durum": FAIL, "skor": 0, "hatalar": ["'kayitlar' bos veya liste degil"], "uyarilar": [], "veri": veri}

    beklenen = BEKLENEN_ALANLAR.get(firma_key, {}).get(tur, [])
    standart = FIRMALAR.get(firma_key, {}).get('adapter') == 'standart_v1'
    sohbet_kaydi = tur == "sohbet_kaydi"
    if standart:
        beklenen = ['Tarih','Proje','Cari_Tipi','Cari','Islem_Turu','Tutar','Hesap_Kasa','PB','Kur']
    elif sohbet_kaydi and not beklenen:
        beklenen = ['Tarih','Faaliyet','Proje','Cari_Tipi','Cari','Islem_Turu','Tutar','PB','Kur']
    if not beklenen:
        uyarilar.append(f"Firma/tur icin sema tanimli degil: {firma_key}/{tur}")
    normalize_edilen = []
    for sira, ham in enumerate(veri["kayitlar"], 1):
        if not isinstance(ham, dict):
            hatalar.append(f"Kayit {sira}: sozluk degil")
            continue
        kayit = _normalize_kayit(ham, beklenen)
        _kanonik_alanlari_tamamla(kayit, firma_key, tur)
        normalize_edilen.append(kayit)
        kredi_taksiti = _norm(kayit.get('Islem_Turu')) == _norm('Kredi Taksit Ödemesi')
        for alan in beklenen:
            if kredi_taksiti and alan == 'Proje' and kayit.get(alan) in (None, ''):
                continue
            if kayit.get(alan) in (None, "", "None"):
                uyarilar.append(f"Kayit {sira}: eksik alan -> {alan}")
        tarih = kayit.get("Tarih") or kayit.get("Vade_Tarihi") or kayit.get("Imza_Tarihi")
        if tarih and not _tarih_gecerli(tarih):
            uyarilar.append(f"Kayit {sira}: gecersiz tarih -> {tarih}")
        if kayit.get("Tutar") not in (None, ""):
            tutar = _tutar_cevir(kayit["Tutar"])
            if tutar is None:
                hatalar.append(f"Kayit {sira}: tutar sayi degil -> {kayit['Tutar']}")
            elif not tutar.is_finite() or tutar <= 0:
                hatalar.append(f"Kayit {sira}: tutar sifir veya negatif -> {kayit['Tutar']}")
            else:
                kayit["Tutar"] = float(tutar)

    veri["kayitlar"] = normalize_edilen
    if not normalize_edilen:
        hatalar.append("Yazilabilir kayit yok")

    if md is None and not hatalar:
        try:
            md = master_data_oku(firma_key)
        except Exception as exc:
            uyarilar.append(f"Master data okunamadi -> {exc}")
    if md:
        for sira, kayit in enumerate(normalize_edilen, 1):
            uyarilar.extend(_master_data_kontrol(kayit, md, firma_key, sira))

    if standart or sohbet_kaydi:
        if md is None:
            hatalar.append('Faaliyet master verisi okunamadı; kayıt durduruldu.')
        for kayit in normalize_edilen:
            kredi_taksiti = _norm(kayit.get('Islem_Turu')) == _norm('Kredi Taksit Ödemesi')
            for alan in beklenen:
                # Banka kredi taksiti faaliyet seviyesinde tutulabilir; proje kodu
                # 04_Kredi & Vade planından gelir ve 03_Kayıt Merkezi'nde boş kalabilir.
                if kredi_taksiti and alan == 'Proje' and kayit.get(alan) in (None, ''):
                    continue
                if kayit.get(alan) in (None, ''):
                    hatalar.append(f'Eksik zorunlu alan: {alan}')
            if not _tarih_gecerli(kayit.get('Tarih')):
                hatalar.append('Geçerli işlem tarihi gerekli.')
            kur = _tutar_cevir(kayit.get('Kur'))
            if kur is None or not kur.is_finite() or kur <= 0:
                hatalar.append('Pozitif ve açıkça belirtilmiş kur gerekli.')
            elif kayit.get('PB') == 'TL' and kur != 1:
                hatalar.append('TL kuru 1 olmalı.')
            else:
                kayit['Kur'] = float(kur)
            if md:
                master_alanlari = [('Faaliyet','faaliyetler'),('Proje','projeler'),
                                   ('Islem_Turu','islem_turleri'),('Cari_Tipi','cari_tipler')]
                if kredi_taksiti and not kayit.get('Proje'):
                    master_alanlari = [x for x in master_alanlari if x[0] != 'Proje']
                yeni_cari = sohbet_kaydi and bool(kayit.get('_yeni_cari'))
                nakit_islemi = any(x in _norm(kayit.get('Islem_Turu')) for x in (
                    'tahsilat', 'odemesi', 'banka masraf', 'kredi kullanimi',
                    'cek teminatli borclanma', 'intercompany'
                ))
                if standart or nakit_islemi:
                    master_alanlari.append(('Hesap_Kasa','hesaplar'))
                for alan, liste in master_alanlari:
                    if yeni_cari and alan == 'Cari_Tipi' and _norm(kayit.get(alan)) == 'diger':
                        continue
                    if not _listede(kayit.get(alan), md[liste]):
                        hatalar.append(f'{alan} seçili faaliyetin tanımlarında bulunamadı.')
                if not _listede(kayit.get('Cari'), md['cariler']):
                    if yeni_cari:
                        uyarilar.append(
                            f"Yeni cari kullanıcı onayıyla tanımlara eklenecek -> {kayit.get('Cari')}"
                        )
                    else:
                        hatalar.append('Cari tanımsız; önce cari tanımlarına ekleyin.')
                elif not any(_norm(x['cari']) == _norm(kayit.get('Cari')) and
                             _norm(x['cari_tipi']) == _norm(kayit.get('Cari_Tipi'))
                             for x in md['cari_hiyerarsisi']):
                    hatalar.append('Cari ve cari tipi eşleşmiyor.')
            if kayit.get('PB') not in ('TL','USD','EUR'):
                hatalar.append('Desteklenmeyen para birimi.')
    hatalar = list(dict.fromkeys(hatalar))
    uyarilar = list(dict.fromkeys(uyarilar))
    skor = max(0, 100 - len(hatalar) * 40 - len(uyarilar) * 8)
    if hatalar:
        durum = FAIL
    elif uyarilar or skor < 85:
        durum = REVIEW
    else:
        durum = PASS
    return {"durum": durum, "skor": skor, "hatalar": hatalar, "uyarilar": uyarilar, "veri": veri}


def dogrula(veri, firma_key, tur):
    """Eski cagiranlar icin (gecerli_mi, uyarilar) uyumlulugunu korur."""
    sonuc = deterministik_dogrula(veri, firma_key, tur)
    mesajlar = sonuc["hatalar"] + sonuc["uyarilar"]
    return sonuc["durum"] != FAIL, mesajlar


def ozet_dogrulama(veri, firma_key, tur):
    sonuc = deterministik_dogrula(veri, firma_key, tur)
    return {
        "gecerli": sonuc["durum"] != FAIL,
        "durum": sonuc["durum"],
        "skor": sonuc["skor"],
        "uyari_sayisi": len(sonuc["uyarilar"]),
        "hata_sayisi": len(sonuc["hatalar"]),
        "uyarilar": sonuc["uyarilar"],
        "hatalar": sonuc["hatalar"],
        "kayit_sayisi": len(veri.get("kayitlar", [])) if isinstance(veri, dict) else 0,
    }
