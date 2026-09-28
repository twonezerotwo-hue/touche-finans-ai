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
    if firma.get('adapter') == 'standart_v1':
        kayit['Faaliyet'] = kayit.get('Faaliyet') or firma['faaliyet']
        kayit['PB'] = kayit.get('PB') or firma['pb']
        kayit['Hesap_Kasa'] = kayit.get('Hesap_Kasa') or firma['varsayilan_hesap']
        if kayit['PB'] == 'TL' and not kayit.get('Kur'):
            kayit['Kur'] = 1
        return
    if firma_key == "gursar_mimarlik":
        kayit.setdefault("Faaliyet", "Gürsar İnşaat")
        kayit.setdefault("PB", "TL")
        kayit.setdefault("Cari_Tipi", _gursar_cari_tipi(kayit.get("Islem_Turu")))
        kayit.setdefault("Hesap_Kasa", "Gürsar TL Banka")
        return

    kayit.setdefault("PB", "TL")
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


def _tutar_cevir(deger):
    if deger in (None, ""):
        return None
    metin = str(deger).strip().replace(" ", "").replace("₺", "")
    if "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        return Decimal(metin)
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
    if cari_tipi and md["cari_tipler"] and not _listede(cari_tipi, md["cari_tipler"]):
        uyarilar.append(f"Kayit {sira}: cari tipi master data'da yok -> {cari_tipi}")
    if cari and md["cariler"] and not _listede(cari, md["cariler"]):
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
    if standart:
        beklenen = ['Tarih','Proje','Cari_Tipi','Cari','Islem_Turu','Tutar','Hesap_Kasa','PB','Kur']
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
        for alan in beklenen:
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

    if standart:
        if md is None:
            hatalar.append('Faaliyet master verisi okunamadı; kayıt durduruldu.')
        for kayit in normalize_edilen:
            for alan in beklenen:
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
                for alan, liste in [('Faaliyet','faaliyetler'),('Proje','projeler'),
                                     ('Hesap_Kasa','hesaplar'),('Islem_Turu','islem_turleri'),('Cari_Tipi','cari_tipler')]:
                    if not _listede(kayit.get(alan), md[liste]):
                        hatalar.append(f'{alan} seçili faaliyetin tanımlarında bulunamadı.')
                if not _listede(kayit.get('Cari'), md['cariler']):
                    hatalar.append('Cari tanımsız; önce /yeni_cari ile ekleyin.')
                elif not any(_norm(x['cari']) == _norm(kayit.get('Cari')) and
                             _norm(x['cari_tipi']) == _norm(kayit.get('Cari_Tipi'))
                             for x in md['cari_hiyerarsisi']):
                    hatalar.append('Cari ve cari tipi eşleşmiyor.')
            if kayit.get('PB') not in ('TL','USD','EUR'):
                hatalar.append('Desteklenmeyen para birimi.')
            if kayit.get('Islem_Turu') == 'Kredi Taksit Ödemesi':
                hatalar.append('Kredi taksiti için faiz/anapara planı entegrasyonu gerekli; bu akışta henüz desteklenmiyor.')
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
