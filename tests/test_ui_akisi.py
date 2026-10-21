# -*- coding: utf-8 -*-
import sys
import base64
import hashlib
import json
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

    def test_excel_onay_parolasi_pbkdf2_ile_dogrulanir(self):
        with tempfile.TemporaryDirectory() as d:
            yol = Path(d) / "onay.json"
            tuz = b"test-tuzu-12345"
            ozet = hashlib.pbkdf2_hmac("sha256", b"123456", tuz, 1000)
            kodla = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")
            yol.write_text(json.dumps({
                "algoritma": "pbkdf2_sha256", "iterasyon": 1000,
                "tuz": kodla(tuz), "ozet": kodla(ozet),
            }), encoding="utf-8")
            with patch.object(belge, "ONAY_PAROLASI_DOSYASI", yol):
                self.assertTrue(belge._onay_parolasi_dogru_mu("123456"))
                self.assertFalse(belge._onay_parolasi_dogru_mu("654321"))

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

    def test_eski_ham_risk_notu_okunur_metne_cevrilir(self):
        belge._BEKLEYEN = {"abc": {
            "token": "abc", "durum": "ONAY_BEKLIYOR", "olusturma": "2026-10-05T12:00:00",
            "kaynak_turu": "sohbet", "hazir": {
                "firma_key": "gursar_mimarlik", "tur": "sohbet_kaydi", "veri": {"kayitlar": []},
                "dogrulama": {
                    "durum": "REVIEW", "skor": 84, "hatalar": [],
                    "uyarilar": ["AI kayıt notu: {'mesaj': '300.000 TL koşullu nakit rezervi gerekebilir.'}"],
                },
            },
        }}
        sonuc = belge.ozet("abc")
        self.assertEqual(
            sonuc["dogrulama"]["uyarilar"],
            ["AI kayıt notu: 300.000 TL koşullu nakit rezervi gerekebilir."],
        )

    def test_kismi_onay_durumu_ozette_korunur(self):
        belge._BEKLEYEN = {"abc": {
            "token": "abc", "durum": "ONAY_BEKLIYOR", "olusturma": "2026-10-05T12:00:00",
            "kaynak_turu": "sohbet",
            "onay": {"gerekli": 2, "mevcut": 1, "tamam": False, "kararlar": [
                {"aktor": "owner@example.com", "rol": "ONAYCI", "karar": "ONAY"}
            ]},
            "hazir": {
                "firma_key": "gursar_mimarlik", "tur": "sohbet_kaydi", "veri": {"kayitlar": []},
                "dogrulama": {"durum": "REVIEW", "skor": 84, "hatalar": [], "uyarilar": []},
            },
        }}
        sonuc = belge.ozet("abc")
        self.assertEqual(sonuc["onay"]["mevcut"], 1)
        self.assertEqual(sonuc["onay"]["gerekli"], 2)

    def test_tamamlanan_cfo_taslaginin_ayni_retrysi_bekleyenlerde_gosterilmez(self):
        ortak = {
            "firma_key": "gursar_mimarlik",
            "tur": "sohbet_kaydi",
            "belge_hash": "aynı-kaynak-hash",
            "normalize_komut": "COUNTERPARTY_PAYMENT|gursar|25755.79|TL",
            "veri": {"kayitlar": []},
            "dogrulama": {"durum": "PASS", "skor": 100, "hatalar": [], "uyarilar": []},
        }
        belge._BEKLEYEN = {
            "pending": {
                "token": "pending", "durum": "ONAY_BEKLIYOR", "olusturma": "2026-10-18T11:46:22",
                "kaynak_turu": "sohbet", "hazir": dict(ortak),
            },
            "committed": {
                "token": "committed", "durum": "TAMAMLANDI", "olusturma": "2026-10-18T11:47:08",
                "kaynak_turu": "sohbet", "hazir": dict(ortak),
            },
        }
        sonuc = belge.listele("sohbet", aktor="local-admin", yonetici=True)
        self.assertEqual([x["token"] for x in sonuc], ["committed"])


if __name__ == "__main__":
    unittest.main()
