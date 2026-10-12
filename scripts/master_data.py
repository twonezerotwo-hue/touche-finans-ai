# -*- coding: utf-8 -*-
"""Firma Excel'lerinden master data okuma (v2 - doğrulanmış sütunlar)."""

import logging
from pathlib import Path
from openpyxl import load_workbook
from config import FIRMALAR, ana_excel_yolu
from faaliyet_sema import anahtar

log = logging.getLogger(__name__)

# Excel tanimlarina ilk onayli kayit sirasinda eklenecek sistem kontrollu
# degerler. Boylece bir kisiden cek teminatiyla alinan para banka kredisi gibi
# raporlanmaz; elden ve sahsi hesaba gelen tutarlar da banka/kasa ayrimini korur.
SISTEM_ISLEM_TURLERI = ["Çek Teminatlı Borçlanma"]
# 99_Ayarlar!U:AA sırasıyla Gelir, Gider, Alacak, Borç, Çek, Nakit ve
# Ortak/IC Cari katsayılarını tutar. Çek karşılığında alınan para gelir değil;
# kasa/bankayı ve aynı tutardaki finansal borcu artırır.
SISTEM_ISLEM_ETKILERI = {
    "Çek Teminatlı Borçlanma": (0, 0, 0, 1, 0, 1, 0),
}
GURSAR_SISTEM_HESAPLARI = [
    "Gürsar TL Kasa",
    "Gürsar USD Kasa",
    "Canfurkan Şensoy TL Banka",
]
GURSAR_SISTEM_PROJELERI = ["Gürsar Faaliyet Geneli"]

# Çöp filtresi (proje/cari listesinden atılacaklar)
GURULTU_KELIMELER = {
    "TOPLAM", "TOTAL", "CİRO", "CIRO", "ALACAK", "BORÇ", "BORC",
    "KONTROL", "ÖZET", "OZET", "GENEL", "ARA TOPLAM",
    "PROJE", "PROJELER", "MALİYET", "MALIYET", "BELGE TARIHI",
    "BELGE TARİHİ", "ALICI", "İŞ KALEMİ", "IS KALEMI",
}


def _gurultu_mu(metin):
    if not metin:
        return True
    ust = str(metin).upper().strip()
    if ust in GURULTU_KELIMELER:
        return True
    # "TOPLAM" / "KONTROL" gibi ibareler geçiyorsa
    for g in ["TOPLAM", "KONTROL", "ÖZET", "OZET", "CİRO", "CIRO"]:
        if g in ust:
            return True
    if len(str(metin).strip()) < 3:
        return True
    return False


def _oku_sutun(ws, sutun_harfi, ilk_satir, son_satir=300, filtre=None):
    """Belirtilen sütundaki boş olmayan değerleri liste olarak döndürür."""
    degerler = []
    for satir in range(ilk_satir, son_satir):
        hucre = ws[f"{sutun_harfi}{satir}"]
        if hucre.value is not None and str(hucre.value).strip() != "":
            d = str(hucre.value).strip()
            if filtre and not filtre(d):
                continue
            degerler.append(d)
    return degerler


def _oku_2d(ws, sutun1, sutun2, ilk_satir, son_satir=300):
    """İki sütunu (key, value) olarak döndürür."""
    ciftler = []
    for satir in range(ilk_satir, son_satir):
        v1 = ws[f"{sutun1}{satir}"].value
        v2 = ws[f"{sutun2}{satir}"].value
        if v1 is not None and str(v1).strip() != "":
            ciftler.append((str(v1).strip(), v2))
    return ciftler


def _oku_3d(ws, sutun1, sutun2, sutun3, ilk_satir, son_satir=300):
    kayitlar = []
    for satir in range(ilk_satir, son_satir):
        v1 = ws[f"{sutun1}{satir}"].value
        v2 = ws[f"{sutun2}{satir}"].value
        v3 = ws[f"{sutun3}{satir}"].value
        if v1 is not None and str(v1).strip() and v3 is not None and str(v3).strip():
            kayitlar.append((str(v1).strip(), v2, str(v3).strip()))
    return kayitlar


def master_data_oku(firma_key):
    if firma_key not in FIRMALAR:
        raise ValueError(f"Bilinmeyen firma: {firma_key}")

    firma = FIRMALAR[firma_key]
    excel_yolu = ana_excel_yolu()

    if not excel_yolu.exists():
        raise FileNotFoundError(f"Excel bulunamadi: {excel_yolu}")

    log.info(f"Master data okunuyor: {firma['ad']}")

    wb = load_workbook(excel_yolu, read_only=False, data_only=True)
    try:
        md_data = _oku_touche(wb)
        if firma_key != "touche_holding":
            md_data = _faaliyete_filtrele(md_data, firma_key)
    finally:
        wb.close()

    log.info(f"Master data OK: {firma['ad']} - "
             f"{len(md_data['faaliyetler'])} faaliyet, "
             f"{len(md_data['projeler'])} proje, "
             f"{len(md_data['cariler'])} cari")

    return md_data


def _oku_touche(wb):
    """Touche Holding 99_Ayarlar - doğrulanmış sütunlar."""
    ws = wb["99_Ayarlar"]
    md = _bos_master_data()

    # Faaliyetler: A13:A32
    md["faaliyetler"] = _oku_sutun(ws, "A", 13, 33,
                                    filtre=lambda x: not _gurultu_mu(x))

    # Projeler: H faaliyet, I proje. Duz listeye ek olarak hiyerarsiyi koru.
    proje_ciftleri = _oku_2d(ws, "H", "I", 13, 113)
    md["proje_hiyerarsisi"] = [
        {"faaliyet": faaliyet, "proje": str(proje).strip()}
        for faaliyet, proje in proje_ciftleri
        if proje is not None and not _gurultu_mu(proje)
    ]
    md["projeler"] = list(dict.fromkeys(
        kayit["proje"] for kayit in md["proje_hiyerarsisi"]
    ))

    # Hesaplar: L faaliyet, M hesap
    hesap_ciftleri = _oku_2d(ws, "L", "M", 13, 63)
    md["hesap_hiyerarsisi"] = [
        {"faaliyet": faaliyet, "hesap": str(hesap).strip()}
        for faaliyet, hesap in hesap_ciftleri
        if hesap is not None and not _gurultu_mu(hesap)
    ]
    md["hesaplar"] = list(dict.fromkeys(
        kayit["hesap"] for kayit in md["hesap_hiyerarsisi"]
    ))

    # Cari tipleri: AH13:AH112
    md["cari_tipler"] = _oku_sutun(ws, "AH", 13, 113,
                                    filtre=lambda x: not _gurultu_mu(x))
    # Tekrarları kaldır
    md["cari_tipler"] = list(dict.fromkeys(md["cari_tipler"]))

    # Cari listesi: AI13:AI112
    md["cariler"] = _oku_sutun(ws, "AI", 13, 113,
                                filtre=lambda x: not _gurultu_mu(x))
    md["cariler"] = list(dict.fromkeys(md["cariler"]))
    cari_ciftleri = _oku_3d(ws, "AG", "AH", "AI", 13, 113)
    md["cari_hiyerarsisi"] = [
        {"faaliyet": faaliyet, "cari_tipi": cari_tipi, "cari": str(cari).strip()}
        for faaliyet, cari_tipi, cari in cari_ciftleri
        if cari is not None and not _gurultu_mu(cari)
    ]

    # İşlem türleri: T6:T22
    md["islem_turleri"] = _oku_sutun(ws, "T", 6, 31,
                                      filtre=lambda x: not _gurultu_mu(x))
    md["islem_turleri"] = list(dict.fromkeys(md["islem_turleri"] + SISTEM_ISLEM_TURLERI))

    # Kurlar: AD3 (PB), AE3 (Kur)
    kur_ciftleri = _oku_2d(ws, "AD", "AE", 3, 8)
    md["kurlar"] = {pb: kur for pb, kur in kur_ciftleri if kur is not None}

    # Benzer bir cari/işlem daha önce doğru şekilde kaydedildiyse sohbet
    # asistanı aynı proje ve hesabı güvenli biçimde önerebilsin. Yalnızca
    # onaylanmış ana defter satırları okunur; tutar ve tarih asla buradan
    # kopyalanmaz.
    md["kayit_gecmisi"] = _oku_ana_ledger_gecmisi(wb)

    return md


def _oku_ana_ledger_gecmisi(wb, limit=1000):
    if "03_Kayıt Merkezi" not in wb.sheetnames:
        return []
    ws = wb["03_Kayıt Merkezi"]
    satirlar = []
    ilk = max(6, ws.max_row - limit + 1)
    for satir in range(ilk, ws.max_row + 1):
        if not ws[f"A{satir}"].value:
            continue
        satirlar.append({
            "faaliyet": str(ws[f"C{satir}"].value or "").strip(),
            "proje": str(ws[f"D{satir}"].value or "").strip(),
            "cari_tipi": str(ws[f"E{satir}"].value or "").strip(),
            "cari": str(ws[f"F{satir}"].value or "").strip(),
            "islem_turu": str(ws[f"G{satir}"].value or "").strip(),
            "hesap_kasa": str(ws[f"H{satir}"].value or "").strip(),
            "pb": str(ws[f"J{satir}"].value or "TL").strip().upper(),
        })
    return satirlar


def _faaliyete_filtrele(md, firma_key):
    """Master data içinden yalnız seçilen faaliyetin hiyerarşisini döndürür."""
    firma = FIRMALAR[firma_key]
    adlar = [firma.get("faaliyet"), firma.get("ad"), *firma.get("faaliyet_aliaslari", [])]
    anahtarlar = {anahtar(ad) for ad in adlar if ad}

    def ait(kayit):
        return anahtar(kayit.get("faaliyet")) in anahtarlar

    sonuc = {**md}
    sonuc["faaliyetler"] = [f for f in md["faaliyetler"] if anahtar(f) in anahtarlar]
    if not sonuc["faaliyetler"]:
        sonuc["faaliyetler"] = [firma.get("faaliyet") or firma["ad"]]
    sonuc["proje_hiyerarsisi"] = [k for k in md["proje_hiyerarsisi"] if ait(k)]
    sonuc["projeler"] = list(dict.fromkeys(k["proje"] for k in sonuc["proje_hiyerarsisi"]))
    sonuc["hesap_hiyerarsisi"] = [k for k in md["hesap_hiyerarsisi"] if ait(k)]
    sonuc["hesaplar"] = list(dict.fromkeys(k["hesap"] for k in sonuc["hesap_hiyerarsisi"]))
    sonuc["cari_hiyerarsisi"] = [k for k in md["cari_hiyerarsisi"] if ait(k)]
    sonuc["cariler"] = list(dict.fromkeys(k["cari"] for k in sonuc["cari_hiyerarsisi"]))
    sonuc["kayit_gecmisi"] = [k for k in md.get("kayit_gecmisi", []) if ait(k)]
    if firma_key == "gursar_mimarlik":
        sonuc["maliyet_gruplari"] = ["Taş işleri", "Saha betonu", "Ortak cari", "Diğer"]
        faaliyet = sonuc["faaliyetler"][0]
        mevcut_projeler = {anahtar(x) for x in sonuc["projeler"]}
        for proje in GURSAR_SISTEM_PROJELERI:
            if anahtar(proje) not in mevcut_projeler:
                sonuc["projeler"].append(proje)
                sonuc["proje_hiyerarsisi"].append({"faaliyet": faaliyet, "proje": proje})
                mevcut_projeler.add(anahtar(proje))
        mevcut_hesaplar = {anahtar(x) for x in sonuc["hesaplar"]}
        for hesap in GURSAR_SISTEM_HESAPLARI:
            if anahtar(hesap) not in mevcut_hesaplar:
                sonuc["hesaplar"].append(hesap)
                sonuc["hesap_hiyerarsisi"].append({"faaliyet": faaliyet, "hesap": hesap})
                mevcut_hesaplar.add(anahtar(hesap))
    return sonuc


def _oku_gursar(wb):
    """Gürsar master data - doğrulanmış kaynaklar."""
    md = _bos_master_data()

    # Projeler: Proje Ciro sayfası, A10:A20
    if "Proje Ciro" in wb.sheetnames:
        ws = wb["Proje Ciro"]
        ham = _oku_sutun(ws, "A", 10, 20,
                         filtre=lambda x: not _gurultu_mu(x))
        md["projeler"] = list(dict.fromkeys(ham))

    if 'AI_Projeler' in wb.sheetnames:
        md['projeler'] = list(dict.fromkeys(md['projeler'] + _oku_sutun(wb['AI_Projeler'], 'A', 2, 101)))

    # Cariler: Ödeme Geçmişi, E6:E50
    if "Ödeme Geçmişi" in wb.sheetnames:
        ws = wb["Ödeme Geçmişi"]
        ham = _oku_sutun(ws, "E", 6, 50,
                         filtre=lambda x: not _gurultu_mu(x))
        md["cariler"] = list(dict.fromkeys(ham))

    # İşlem türleri (Gürsar sabit 4 tür)
    md["islem_turleri"] = [
        "Usta ödemesi",
        "Ortak cari ödemesi",
        "Banka masrafı",
        "Diğer",
    ]

    # Maliyet grupları
    md["maliyet_gruplari"] = [
        "Taş işleri",
        "Saha betonu",
        "Ortak cari",
        "Diğer",
    ]

    # Faaliyetler
    md["faaliyetler"] = ["Gürsar Mimarlık", "Gürsar İnşaat"]
    md["proje_hiyerarsisi"] = [
        {"faaliyet": "Gürsar İnşaat", "proje": proje}
        for proje in md["projeler"]
    ]

    # Kurlar boş - TCMB'den canlı çekilecek
    md["kurlar"] = {}

    return md


def _oku_standart(wb, firma):
    ws = wb['Tanımlar']
    md = _bos_master_data()
    faaliyet = firma['faaliyet']
    md['faaliyetler'] = [faaliyet]
    for key, col, son in [('projeler','A',56), ('hesaplar','C',56),
                           ('cari_tipler','E',26), ('islem_turleri','G',26), ('cariler','P',106)]:
        md[key] = _oku_sutun(ws, col, 6, son)
    md['kurlar'] = dict(_oku_2d(ws, 'L', 'M', 6, 16))
    md['proje_hiyerarsisi'] = [{'faaliyet':faaliyet, 'proje':p} for p in md['projeler']]
    md['hesap_hiyerarsisi'] = [{'faaliyet':faaliyet, 'hesap':h} for h in md['hesaplar']]
    md['cari_hiyerarsisi'] = [{'cari_tipi':t, 'cari':c} for t,c in _oku_2d(ws,'O','P',6,106) if c]
    return md


def _bos_master_data():
    return {
        "faaliyetler": [],
        "projeler": [],
        "hesaplar": [],
        "cariler": [],
        "cari_tipler": [],
        "islem_turleri": [],
        "kurlar": {},
        "maliyet_gruplari": [],
        "proje_hiyerarsisi": [],
        "hesap_hiyerarsisi": [],
        "cari_hiyerarsisi": [],
        "kayit_gecmisi": [],
    }


def master_data_ozet(firma_key):
    md = master_data_oku(firma_key)
    return {
        "faaliyet_sayisi": len(md["faaliyetler"]),
        "proje_sayisi": len(md["projeler"]),
        "cari_sayisi": len(md["cariler"]),
        "cari_tip_sayisi": len(md["cari_tipler"]),
        "hesap_sayisi": len(md["hesaplar"]),
        "islem_turu_sayisi": len(md["islem_turleri"]),
        "kur_sayisi": len(md["kurlar"]),
        "faaliyetler": md["faaliyetler"][:5],
        "projeler": md["projeler"][:5],
        "cariler": md["cariler"][:5],
        "cari_tipler": md["cari_tipler"][:5],
        "islem_turleri": md["islem_turleri"][:5],
        "kurlar": md["kurlar"],
    }
