# -*- coding: utf-8 -*-
"""Proje / faaliyet / holding metrikleri: deterministik fixture ile elle hesaplanmis degerler.

Kritik ayrimlar: gelir != tahsilat, gider != odeme, kar != nakit, grup ici transfer gider degil.
"""
import sqlite3
import unittest

import finans_cekirdegi as cekirdek
import kurumsal_finans as kurumsal
import proje_finansi as pf
from cfo_test_temeli import CfoTestTemeli


class ProjeMetrikTestleri(CfoTestTemeli):
    """Gokturk Okulu fixture (TL):

    1. Hakedis (gelir tahakkuku)     500.000   -> Dr 120 / Cr 600
    2. Tahsilat                      200.000   -> Dr 102 / Cr 120
    3. Gider tahakkuku (Elektrik)    300.000   -> Dr 740 / Cr 320   (is kalemi KLM-001)
    4. Odeme (Devrim)                100.000   -> Dr 320 / Cr 102   (is kalemi KLM-001)
    5. Odeme (Sebahattin)             40.000   -> Dr 320 / Cr 102
    """

    def _fis(self, belge, tarih, islem, tutar, cari, hesap="Gürsar TL Banka", proje="Göktürk Okulu", kalem=None,
             faaliyet="gursar_mimarlik"):
        kayit = {"Tarih": tarih, "Islem_Turu": islem, "Tutar": tutar, "PB": "TL", "Kur": 1, "Cari": cari,
                 "Proje": proje, "Hesap_Kasa": hesap, "Aciklama": f"{islem} {cari}"}
        if kalem:
            kayit["Is_Kalemi_ID"] = kalem
        cekirdek.finans_kaydi_yaz({"belge_id": belge, "firma_key": faaliyet, "tur": "finans_belgesi",
                                   "veri": {"kayitlar": [kayit]}}, "test")

    def setUp(self):
        super().setUp()
        self._fis("H1", "2026-09-01", "Hakediş / Gelir Tahakkuku", 500_000, "İşveren")
        self._fis("T1", "2026-09-10", "Tahsilat", 200_000, "İşveren")
        self._fis("G1", "2026-09-15", "Gider Tahakkuku", 300_000, "Devrim", kalem="KLM-001")
        self._fis("O1", "2026-09-20", "Usta/Tedarikçi Ödemesi", 100_000, "Devrim", kalem="KLM-001")
        self._fis("O2", "2026-09-22", "Usta/Tedarikçi Ödemesi", 40_000, "Sebahattin Çakmak")
        # Baska proje: Goktürk metriklerine karismamali
        self._fis("D1", "2026-09-05", "Gider Tahakkuku", 77_000, "Devrim", proje="Bahçeşehir Villa")

    def detay(self):
        return pf.proje_detay("gursar_mimarlik", "Göktürk Okulu", self.katalog())

    def deger(self, d, ad):
        return d["metrikler"][ad]["deger_kurus"]

    def test_gelir_tahsilat_gider_odeme_kar_nakit_ayri_metriklerdir(self):
        d = self.detay()
        TL = 100
        self.assertEqual(self.deger(d, "kazanilan_gelir"), 500_000 * TL)       # kazanılan
        self.assertEqual(self.deger(d, "tahsil_edilen"), 200_000 * TL)         # tahsil edilen != kazanılan
        self.assertEqual(self.deger(d, "kalan_alacak"), 300_000 * TL)          # 500 - 200
        self.assertEqual(self.deger(d, "gider"), 300_000 * TL)                 # tahakkuk
        self.assertEqual(self.deger(d, "odenen"), 140_000 * TL)                # ödenen != gider
        self.assertEqual(self.deger(d, "acik_borc"), 160_000 * TL)             # 300 - 140
        self.assertEqual(self.deger(d, "kar_zarar"), 200_000 * TL)             # 500 - 300  (kâr)
        self.assertEqual(self.deger(d, "net_nakit"), 60_000 * TL)              # 200 - 140  (nakit) != kâr
        self.assertNotEqual(self.deger(d, "kar_zarar"), self.deger(d, "net_nakit"))
        self.assertEqual(d["metrikler"]["kar_marji"]["deger"], 40.0)           # 200/500
        self.assertEqual(d["uyarilar"], [])

    def test_diger_proje_karismaz(self):
        d = pf.proje_detay("gursar_mimarlik", "Bahçeşehir Villa", self.katalog())
        self.assertEqual(self.deger(d, "gider"), 77_000 * 100)
        self.assertEqual(self.deger(d, "kazanilan_gelir"), 0)
        self.assertEqual(d["metrikler"]["kar_marji"]["durum"], pf.DNA)         # gelir 0 -> marj uydurulmaz
        self.assertEqual(self.deger(d, "kar_zarar"), -77_000 * 100)

    def test_proje_adi_buyuk_kucuk_harf_ve_aksan_duyarsiz(self):
        d = pf.proje_detay("gursar_mimarlik", "GOKTURK okulu", self.katalog())
        self.assertEqual(self.deger(d, "gider"), 300_000 * 100)

    def test_is_kalemleri_planli_gerceklesen_odenen_kalan(self):
        d = self.detay()
        elektrik = next(k for k in d["is_kalemleri"] if k["kalem_id"] == "KLM-001")
        self.assertEqual(elektrik["maliyet_plani_kurus"], 250_000 * 100)
        self.assertEqual(elektrik["gerceklesen_gider_kurus"], 300_000 * 100)
        self.assertEqual(elektrik["odenen_kurus"], 100_000 * 100)
        self.assertEqual(elektrik["kalan_odeme_kurus"], 200_000 * 100)
        self.assertEqual(elektrik["butce_kalan_kurus"], -50_000 * 100)         # planı aştı
        self.assertEqual(elektrik["ilerleme"], "Devam ediyor")
        basamak = next(k for k in d["is_kalemleri"] if k["kalem_id"] == "KLM-002")
        self.assertEqual(basamak["ilerleme"], "Başlamadı")
        self.assertEqual(basamak["bagli_kayit_sayisi"], 0)
        self.assertEqual(d["is_kalemi_ozeti"]["toplam"], 3)

    def test_sozlesme_degeri_onayli_is_kalemlerinden_taslak_dahil_degil(self):
        d = self.detay()
        # Elektrik 400.000 + Basamak 100*5.000 = 900.000 ; Saha betonu TASLAK -> dahil değil
        self.assertEqual(self.deger(d, "sozlesme_gelir_degeri"), 900_000 * 100)
        self.assertIn("onaylı iş kalemleri", d["metrikler"]["sozlesme_gelir_degeri"]["kaynak"])

    def test_cari_bakiyeleri_ve_son_islemler(self):
        d = self.detay()
        devrim = next(c for c in d["cari_bakiyeleri"] if c["cari"] == "Devrim")
        self.assertEqual((devrim["odenen_kurus"], devrim["acik_borc_kurus"]), (100_000 * 100, 200_000 * 100))
        self.assertEqual([x["tur"] for x in d["son_islemler"]][:2], ["Ödeme", "Ödeme"])
        self.assertEqual(len(d["son_islemler"]), 5)

    def test_odeme_tahakkuksuzsa_uyarir_gider_sifir_gorunmesin(self):
        self._fis("O9", "2026-10-01", "Usta/Tedarikçi Ödemesi", 10_000, "Devrim", proje="Bahçeşehir Villa")
        d = pf.proje_detay("gursar_mimarlik", "Bahçeşehir Villa", self.katalog())
        self.assertEqual(self.deger(d, "odenen"), 10_000 * 100)
        self.assertEqual(self.deger(d, "acik_borc"), 77_000 * 100 - 10_000 * 100)
        # Tahakkuksuz proje
        self._fis("O8", "2026-10-02", "Usta/Tedarikçi Ödemesi", 50_000, "Devrim", faaliyet="gursar_mimarlik",
                  proje="Göktürk Okulu", kalem=None)
        d2 = self.detay()
        self.assertEqual(self.deger(d2, "acik_borc"), 110_000 * 100)

    def test_ters_kayit_metrikleri_geri_alir(self):
        once = self.deger(self.detay(), "odenen")
        cekirdek.belgeyi_ters_kaydet("O2", "test")
        d = self.detay()
        self.assertEqual(self.deger(d, "odenen"), once - 40_000 * 100)
        self.assertEqual(self.deger(d, "acik_borc"), 200_000 * 100)
        self.assertNotIn("O2", [x["belge_id"] for x in d["son_islemler"]])

    def test_tahsilat_iptali_nakit_cikisi_odeme_sayilmaz(self):
        cekirdek.belgeyi_ters_kaydet("T1", "test")
        d = self.detay()
        self.assertEqual(self.deger(d, "tahsil_edilen"), 0)
        self.assertEqual(self.deger(d, "odenen"), 140_000 * 100)          # tahsilat iptali ödeme olmadı

    def test_kredi_ve_tedarikci_iadesi_dogru_siniflanir(self):
        self._fis("K1", "2026-09-25", "Kredi Kullanımı", 90_000, "Banka", proje="Göktürk Okulu")
        d = self.detay()
        self.assertEqual(self.deger(d, "tahsil_edilen"), 200_000 * 100)    # kredi tahsilat değil
        self.assertEqual(self.deger(d, "net_nakit"), 60_000 * 100)

    def test_faaliyet_detayi_projeleri_ve_toplami_icerir(self):
        d = pf.faaliyet_detay("gursar_mimarlik", self.katalog())
        self.assertEqual(d["faaliyet"], "Gürsar Mimarlık")
        adlar = {p["proje"]: p for p in d["projeler"]}
        self.assertEqual(set(adlar), {"Göktürk Okulu", "Bahçeşehir Villa"})
        self.assertEqual(adlar["Göktürk Okulu"]["kazanilan_gelir"]["deger_kurus"], 500_000 * 100)
        self.assertEqual(d["metrikler"]["gider"]["deger_kurus"], (300_000 + 77_000) * 100)
        self.assertEqual(d["metrikler"]["odenen"]["deger_kurus"], 140_000 * 100)

    def test_proje_listesi_tum_projeleri_dondurur(self):
        l = pf.proje_listesi(None, self.katalog())["projeler"]
        self.assertEqual({(p["faaliyet_key"], p["proje"]) for p in l},
                         {("gursar_mimarlik", "Göktürk Okulu"), ("gursar_mimarlik", "Bahçeşehir Villa")})

    def test_bilinmeyen_proje_hata(self):
        with self.assertRaises(ValueError):
            pf.proje_detay("gursar_mimarlik", "Uydurma", self.katalog())

    def test_okuma_baglantisi_yazmayi_motor_duzeyinde_engeller(self):
        with pf.okuma_baglantisi() as con:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM fisler")
        with cekirdek._baglanti() as con:
            self.assertGreater(con.execute("SELECT COUNT(*) FROM fisler").fetchone()[0], 0)


class HoldingKonsolidasyonTestleri(CfoTestTemeli):
    def _fis(self, belge, faaliyet, tarih, islem, tutar, cari, hesap="Banka", proje="X"):
        kayit = {"Tarih": tarih, "Islem_Turu": islem, "Tutar": tutar, "PB": "TL", "Kur": 1, "Cari": cari,
                 "Proje": proje, "Hesap_Kasa": hesap, "Aciklama": islem}
        cekirdek.finans_kaydi_yaz({"belge_id": belge, "firma_key": faaliyet, "tur": "finans_belgesi",
                                   "veri": {"kayitlar": [kayit]}}, "test")

    def test_holding_gercek_faaliyet_kayitlarindan_toplanir_grup_ici_elimine(self):
        self._fis("A1", "gursar_mimarlik", "2026-09-01", "Hakediş / Gelir Tahakkuku", 100_000, "İşveren")
        self._fis("A2", "touche_holding", "2026-09-02", "Gider Tahakkuku", 30_000, "Ofis Market")
        self._fis("A3", "touche_holding", "2026-09-03", "Usta/Tedarikçi Ödemesi", 10_000, "Ofis Market")
        once = pf.holding_ozeti(self.katalog())
        # 250.000 TL grup içi transfer: iki faaliyet fişi (Dr 131 / Cr nakit ; Dr nakit / Cr 331)
        kurumsal.sirketler_arasi_transfer("gursar_mimarlik", "touche_holding", "2026-09-15", 250_000, "Sermaye", "test")
        sonra = pf.holding_ozeti(self.katalog())
        for ad in ("kazanilan_gelir", "gider", "odenen", "tahsil_edilen", "kalan_alacak", "acik_borc", "kar_zarar", "net_nakit"):
            self.assertEqual(sonra["konsolide"][ad]["deger_kurus"], once["konsolide"][ad]["deger_kurus"], ad)
        self.assertEqual(sonra["konsolide"]["gider"]["deger_kurus"], 30_000 * 100)
        self.assertEqual(sonra["konsolide"]["odenen"]["deger_kurus"], 10_000 * 100)
        # Faaliyet satırları gerçek kayıtlardan: transfer ödeme/gider sayılmadı
        satir = {f["faaliyet_key"]: f for f in sonra["faaliyetler"]}
        self.assertEqual(satir["gursar_mimarlik"]["metrikler"]["odenen"]["deger_kurus"], 0)
        self.assertEqual(satir["touche_holding"]["metrikler"]["gider"]["deger_kurus"], 30_000 * 100)
        self.assertEqual(satir["gursar_mimarlik"]["metrikler"]["kazanilan_gelir"]["deger_kurus"], 100_000 * 100)

    def test_her_faaliyet_ayni_genel_mimariyle_listelenir(self):
        d = pf.holding_ozeti(self.katalog())
        self.assertEqual({f["faaliyet_key"] for f in d["faaliyetler"]}, {"touche_holding", "gursar_mimarlik"})

    def test_yeni_faaliyet_ayni_mimariyle_calisir(self):
        import faaliyet_yonetimi as fy
        from unittest.mock import patch

        with patch.object(fy, "FAALIYET_REGISTRY", self.root / "registry.json"):
            key = fy.faaliyet_olustur("Touche Enerji", "Silivri GES", "Enerji TL Banka", "TL", 100)
        import master_data

        master_data.onbellegi_temizle()
        kat = self.katalog()
        self.assertIn(key, {f.kimlik for f in kat.faaliyetler})
        self._fis("E1", key, "2026-09-01", "Hakediş / Gelir Tahakkuku", 40_000, "Müşteri E", proje="Silivri GES")
        d = pf.holding_ozeti(kat)
        satir = {f["faaliyet_key"]: f for f in d["faaliyetler"]}
        self.assertEqual(satir[key]["metrikler"]["kazanilan_gelir"]["deger_kurus"], 40_000 * 100)
        self.assertEqual(d["konsolide"]["kazanilan_gelir"]["deger_kurus"], 40_000 * 100)


if __name__ == "__main__":
    unittest.main()
