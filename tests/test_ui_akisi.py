# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ui_belge_servisi as belge
import ui_yetki as yetki


class UiYetkiTestleri(unittest.TestCase):
    def test_yerel_oturum_yetkili(self):
        sonuc = yetki.oturum({}, "127.0.0.1:8765", "127.0.0.1")
        self.assertTrue(sonuc["yetkili"])
        self.assertTrue(sonuc["yerel"])

    def test_uzak_kullanici_eslestirme_ister(self):
        with tempfile.TemporaryDirectory() as d, patch.object(yetki, "YETKI_DOSYASI", Path(d) / "users.json"):
            sonuc = yetki.oturum({"Tailscale-User-Login": "owner@example.com"}, "pc.tailnet.ts.net", "127.0.0.1")
        self.assertFalse(sonuc["yetkili"])
        self.assertTrue(sonuc["eslestirme_gerekli"])

    def test_kod_ile_kullanici_eslesir(self):
        with tempfile.TemporaryDirectory() as d, patch.object(yetki, "YETKI_DOSYASI", Path(d) / "users.json"):
            kod = yetki.kod_uret()["kod"]
            yetki.eslestir(kod, "Owner@Example.com")
            self.assertEqual(yetki.yetkililer(), ["owner@example.com"])

    def test_aktor_numarasi_kararli(self):
        self.assertEqual(yetki.aktor_numarasi("x@y.com"), yetki.aktor_numarasi("x@y.com"))
        self.assertLess(yetki.aktor_numarasi("x@y.com"), 0)


class UiBelgeTestleri(unittest.TestCase):
    def setUp(self):
        belge._BEKLEYEN = {}

    def test_dosya_adi_dizin_gecisini_engeller(self):
        self.assertEqual(belge._guvenli_ad("../../fatura 1.pdf"), "fatura_1.pdf")

    def test_desteklenmeyen_uzanti_reddedilir(self):
        with self.assertRaises(ValueError):
            belge.yuklenen_dosyayi_kaydet("zararli.exe", b"test")

    def test_hazir_belge_onay_kuyruguna_girer(self):
        hazir = {
            "basari": True, "foto_yolu": "C:/temp/fatura.pdf", "model_yolu": "C:/temp/fatura.jpg",
            "firma_key": "touche_holding", "tur": "fatura", "belge_id": "TEST-1",
            "dogrulama": {"durum": "PASS", "skor": 100, "uyarilar": [], "hatalar": []},
            "veri": {"kayitlar": [{"Tarih": "2026-09-27", "Cari": "Test", "Tutar": 100, "PB": "TL"}]},
        }
        with tempfile.TemporaryDirectory() as d, patch.object(belge, "STATE_PATH", Path(d) / "state.json"), \
             patch.object(belge, "belge_dosyasi_hazirla", return_value=hazir):
            sonuc = belge.belgeyi_hazirla(Path(d) / "fatura.pdf", "touche_holding", "test-user")
        self.assertEqual(sonuc["durum"], "ONAY_BEKLIYOR")
        self.assertEqual(sonuc["dogrulama"]["durum"], "PASS")


if __name__ == "__main__":
    unittest.main()
