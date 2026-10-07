# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finans_cekirdegi as cekirdek
import guvenlik
import kurumsal_finans as kurumsal


class KurumsalFinansTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(cekirdek, "DB_PATH", self.root / "finans.db"),
            patch.object(guvenlik, "AUDIT_LOG", self.root / "audit.jsonl"),
            patch.object(guvenlik, "LOGLAR", self.root),
        ]
        for p in self.patches:
            p.start()
        cekirdek.veritabanini_hazirla()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_sirketler_arasi_transfer_iki_fis_ve_eliminasyon_olusturur(self):
        sonuc = kurumsal.sirketler_arasi_transfer(
            "touche_holding", "gursar_mimarlik", "28.09.2026", 10_000,
            "İşletme sermayesi", "test",
        )
        self.assertTrue(sonuc["kaynak_fis"].startswith("JRN-IC-"))
        mutabakat = kurumsal.sirketler_arasi_mutabakat()
        self.assertEqual(mutabakat[0]["durum"], "UYUMLU")
        mizan = {x["hesap"]: x for x in kurumsal.konsolide_mizan("01.09.2026", "30.09.2026")}
        self.assertEqual(mizan["131"]["borc"], 0)
        self.assertEqual(mizan["331"]["alacak"], 0)
        self.assertEqual(sum(x["borc"] for x in mizan.values()), sum(x["alacak"] for x in mizan.values()))

    def test_hesap_esleme_konsolide_mizanda_kullanilir(self):
        kurumsal.hesap_eslemesi_kaydet("gursar_mimarlik", "799", "740", "test")
        hazir = {"belge_id": "MAP-1", "firma_key": "gursar_mimarlik", "tur": "bilinmeyen", "veri": {"kayitlar": [{
            "Tarih": "28.09.2026", "Cari": "X", "Tutar": 500,
        }]}}
        cekirdek.finans_kaydi_yaz(hazir, "test")
        mizan = {x["hesap"]: x for x in kurumsal.konsolide_mizan("01.09.2026", "30.09.2026")}
        self.assertIn("740", mizan)
        self.assertNotIn("799", mizan)

    def test_cek_teminatli_borclanma_kasa_ve_bankayi_336_ile_esler(self):
        hazir = {"belge_id": "CEK-BORC-1", "firma_key": "gursar_mimarlik", "tur": "sohbet_kaydi", "veri": {"kayitlar": [
            {"Tarih": "04.10.2026", "Islem_Turu": "Çek Teminatlı Borçlanma", "Hesap_Kasa": "Gürsar TL Kasa", "Tutar": 32000, "PB": "TL", "Kur": 1},
            {"Tarih": "05.10.2026", "Islem_Turu": "Çek Teminatlı Borçlanma", "Hesap_Kasa": "Canfurkan Şensoy TL Banka", "Tutar": 74000, "PB": "TL", "Kur": 1},
        ]}}
        taslaklar = cekirdek.fis_taslaklari(hazir)
        self.assertEqual([x["hesap"] for x in taslaklar[0]["satirlar"]], ["100", "336"])
        self.assertEqual([x["hesap"] for x in taslaklar[1]["satirlar"]], ["102", "336"])

    def test_ortak_cari_odemesi_elden_yapildiysa_kasadan_duser(self):
        hazir = {"belge_id": "ORTAK-NAKIT-1", "firma_key": "gursar_mimarlik", "tur": "sohbet_kaydi", "veri": {"kayitlar": [{
            "Tarih": "06.10.2026", "Proje": "Göktürk Okulu", "Cari": "Devrim",
            "Cari_Tipi": "Ortak", "Islem_Turu": "Ortak Cari Ödemesi",
            "Hesap_Kasa": "Gürsar TL Kasa", "Tutar": 20000, "PB": "TL", "Kur": 1,
        }]}}
        taslak = cekirdek.fis_taslaklari(hazir)[0]
        self.assertEqual([x["hesap"] for x in taslak["satirlar"]], ["331", "100"])

    def test_cari_yaslandirma_tahsilati_fifo_duser(self):
        fatura = {"belge_id": "AR-1", "firma_key": "gursar_mimarlik", "tur": "hakedis", "veri": {"kayitlar": [{
            "Tarih": "01.08.2026", "Vade": "01.08.2026", "Cari": "Müşteri A",
            "Islem_Turu": "Hakediş", "Tutar": 1000,
        }]}}
        tahsilat = {"belge_id": "AR-2", "firma_key": "gursar_mimarlik", "tur": "dekont", "veri": {"kayitlar": [{
            "Tarih": "15.09.2026", "Cari": "Müşteri A", "Islem_Turu": "Tahsilat", "Tutar": 400,
        }]}}
        cekirdek.finans_kaydi_yaz(fatura, "test")
        cekirdek.finans_kaydi_yaz(tahsilat, "test")
        rapor = kurumsal.cari_yaslandirma("120", "gursar_mimarlik", "30.09.2026")
        self.assertEqual(rapor[0]["toplam"], 600)
        self.assertEqual(rapor[0]["31_60"], 600)

    def test_butce_sapmasi_gerceklesenle_karsilastirilir(self):
        cekirdek.butce_kaydet("gursar_mimarlik", "Göktürk Okulu", "740", 2026, 9, 1000, "test")
        cekirdek.finans_kaydi_yaz({"belge_id": "BUD-1", "firma_key": "gursar_mimarlik", "tur": "fatura", "veri": {"kayitlar": [{
            "Tarih": "20.09.2026", "Proje": "Göktürk Okulu", "Tedarikci": "A", "Toplam": 750,
        }]}}, "test")
        rapor = kurumsal.butce_sapma_raporu("gursar_mimarlik", "01.09.2026", "30.09.2026")
        self.assertEqual(rapor["kalemler"][0]["gerceklesen"], 750)
        self.assertEqual(rapor["kalemler"][0]["sapma"], -250)
        self.assertEqual(rapor["kalemler"][0]["durum"], "OLUMLU")

    def test_kur_degerleme_kambiyo_fisi_olusturur_ve_idempotenttir(self):
        cekirdek.finans_kaydi_yaz({"belge_id": "FXSRC-1", "firma_key": "gursar_mimarlik", "tur": "fatura", "veri": {"kayitlar": [{
            "Tarih": "01.09.2026", "Tedarikci": "Euro Tedarikçi", "Toplam": 100, "PB": "EUR", "Kur": 40,
        }]}}, "test")
        kurumsal.kur_kaydet("30.09.2026", "EUR", 42, "test")
        ilk = kurumsal.kur_degerleme("30.09.2026", "test")
        ikinci = kurumsal.kur_degerleme("30.09.2026", "test")
        self.assertEqual(len(ilk["olusan"]), 1)
        self.assertAlmostEqual(ilk["olusan"][0]["fark"], 200)
        self.assertEqual(len(ikinci["olusan"]), 0)

    def test_onay_matrisi_rol_tutar_ve_gorev_ayriligini_uygular(self):
        kurumsal.onay_kurali_kaydet("TEST-YUKSEK", "gursar_mimarlik", 1000, None, 2,
                                    ["ONAYCI", "ADMIN"], True, "test")
        with self.assertRaises(PermissionError):
            kurumsal.onay_karari_ekle("APR-1", "gursar_mimarlik", 1500, "hazirlayan",
                                      "ONAYCI", "hazirlayan")
        bir = kurumsal.onay_karari_ekle("APR-1", "gursar_mimarlik", 1500, "onayci-1",
                                       "ONAYCI", "hazirlayan")
        iki = kurumsal.onay_karari_ekle("APR-1", "gursar_mimarlik", 1500, "onayci-2",
                                       "ADMIN", "hazirlayan")
        self.assertFalse(bir["tamam"])
        self.assertTrue(iki["tamam"])

    def test_belge_manifesti_ayni_kimlikte_icerik_degisimini_reddeder(self):
        hazir = {"belge_id": "IMM-1", "belge_hash": "a" * 64, "firma_key": "gursar_mimarlik", "tur": "fatura", "veri": {"kayitlar": [{
            "Tarih": "28.09.2026", "Tedarikci": "A", "Toplam": 100,
        }]}}
        cekirdek.finans_kaydi_yaz(hazir, "test")
        degisen = {**hazir, "belge_hash": "b" * 64}
        with self.assertRaises(ValueError):
            cekirdek.finans_kaydi_yaz(degisen, "test")

    def test_audit_hash_zinciri_degistirmeyi_bulur(self):
        guvenlik.audit_yaz("bir", deger=1)
        guvenlik.audit_yaz("iki", deger=2)
        self.assertTrue(guvenlik.audit_zincirini_dogrula()["gecerli"])
        yol = self.root / "audit.jsonl"
        yol.write_text(yol.read_text(encoding="utf-8").replace('"deger": 1', '"deger": 9'), encoding="utf-8")
        self.assertFalse(guvenlik.audit_zincirini_dogrula()["gecerli"])

    def test_donem_kapanis_kontrolu_engel_ve_uyarilari_ayirir(self):
        kontrol = kurumsal.donem_kapanis_kontrolu(2026, 9)
        self.assertTrue(kontrol["kapanabilir"])
        self.assertEqual(kontrol["dengesiz_fis"], 0)


if __name__ == "__main__":
    unittest.main()
