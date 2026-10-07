# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sohbet_kaydi as sk
import ui_belge_servisi as ui


MD = {
    "faaliyetler": ["Gürsar İnşaat"],
    "projeler": ["Göktürk Okulu"],
    "hesaplar": ["Gürsar TL Banka", "Gürsar EUR Banka", "Gürsar TL Kasa", "Gürsar USD Kasa", "Canfurkan Şensoy TL Banka"],
    "cariler": ["Sebahattin Çakmak", "İşveren"],
    "cari_tipler": ["Usta", "Müşteri"],
    "islem_turleri": ["Hakediş / Gelir Tahakkuku", "Tahsilat", "Gider Tahakkuku", "Usta/Tedarikçi Ödemesi", "Kredi Kullanımı", "Çek Teminatlı Borçlanma"],
    "kurlar": {"TL": 1, "EUR": 55.0},
    "maliyet_gruplari": [],
    "proje_hiyerarsisi": [{"faaliyet": "Gürsar İnşaat", "proje": "Göktürk Okulu"}],
    "hesap_hiyerarsisi": [{"faaliyet": "Gürsar İnşaat", "hesap": "Gürsar TL Banka"}],
    "cari_hiyerarsisi": [
        {"faaliyet": "Gürsar İnşaat", "cari_tipi": "Usta", "cari": "Sebahattin Çakmak"},
        {"faaliyet": "Gürsar İnşaat", "cari_tipi": "Müşteri", "cari": "İşveren"},
    ],
    "kayit_gecmisi": [],
}


class SohbetKaydiTestleri(unittest.TestCase):
    def setUp(self):
        ui._BEKLEYEN = {}

    def test_rapor_sorusu_kayit_komutu_sayilmaz(self):
        self.assertFalse(sk.kayit_niyeti_mi("Eylül ödemelerini kalem kalem göster"))
        self.assertFalse(sk.kayit_niyeti_mi("Gelir gider yazdırabilir miyiz?"))
        self.assertFalse(sk.kayit_niyeti_mi("Çek Excel'e işlendi mi?"))
        self.assertTrue(sk.kayit_niyeti_mi("Gürsar için bugün 10000 TL ödeme kaydet"))
        self.assertTrue(sk.kayit_niyeti_mi(
            "Gürsar Göktürk Okulu çek işlemini Cemre Şensoy ve Tuana Battal için Excel'e işleyelim"
        ))

    def test_llm_sadece_cikarir_master_data_kanoniklestirir(self):
        ai = {"kayitlar": [{
            "Tarih": "2026-10-02", "Cari": "Sebahattin", "Islem_Turu": "ödeme",
            "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 10000, "PB": "TL",
        }]}
        with patch.object(sk, "master_data_oku", return_value=MD), \
             patch.object(sk, "metne_sor", return_value=ai), patch.object(sk, "audit_yaz"):
            hazir = sk.taslak_hazirla("Gürsar Sebahattin'e 10000 TL ödeme kaydet", "gursar_mimarlik")
        kayit = hazir["veri"]["kayitlar"][0]
        self.assertEqual(kayit["Faaliyet"], "Gürsar İnşaat")
        self.assertEqual(kayit["Proje"], "Göktürk Okulu")
        self.assertEqual(kayit["Cari"], "Sebahattin Çakmak")
        self.assertEqual(kayit["Cari_Tipi"], "Usta")
        self.assertEqual(kayit["Islem_Turu"], "Usta/Tedarikçi Ödemesi")
        self.assertEqual(kayit["Hesap_Kasa"], "Gürsar TL Banka")
        self.assertEqual(hazir["dogrulama"]["durum"], "PASS")

    def test_benzer_cari_tekil_proje_ve_hesabi_otomatik_eslestirir(self):
        md = {**MD, "projeler": ["Göktürk Okulu", "Gürsar Faaliyet Geneli"], "kayit_gecmisi": [{
            "faaliyet": "Gürsar İnşaat", "proje": "Göktürk Okulu", "cari_tipi": "Usta",
            "cari": "Sebahattin Çakmak", "islem_turu": "Usta/Tedarikçi Ödemesi",
            "hesap_kasa": "Gürsar TL Banka", "pb": "TL",
        }]}
        ai = {"kayitlar": [{
            "Tarih": "2026-10-06", "Cari": "Sebahattin Çakmak",
            "Islem_Turu": "Usta/Tedarikçi Ödemesi", "Tutar": 10000, "PB": "TL",
        }]}
        with patch.object(sk, "master_data_oku", return_value=md), \
             patch.object(sk, "metne_sor", return_value=ai), patch.object(sk, "audit_yaz"):
            hazir = sk.taslak_hazirla(
                "Gürsar için Sebahattin Çakmak'a 10.000 TL ödeme kaydet",
                "gursar_mimarlik",
            )
        kayit = hazir["veri"]["kayitlar"][0]
        self.assertEqual(kayit["Proje"], "Göktürk Okulu")
        self.assertEqual(kayit["Hesap_Kasa"], "Gürsar TL Banka")
        self.assertEqual(len(kayit["_otomatik_eslesmeler"]), 2)
        self.assertEqual(hazir["dogrulama"]["durum"], "PASS")

    def test_belirsiz_odeme_hesabi_tahmin_edilmez_ve_kullaniciya_sorulur(self):
        ai = {"kayitlar": [{
            "Tarih": "2026-10-06", "Proje": "Göktürk Okulu", "Cari": "Sebahattin Çakmak",
            "Islem_Turu": "Usta/Tedarikçi Ödemesi", "Tutar": 10000, "PB": "TL",
        }]}
        with patch.object(sk, "master_data_oku", return_value=MD), \
             patch.object(sk, "metne_sor", return_value=ai), patch.object(sk, "audit_yaz"):
            hazir = sk.taslak_hazirla(
                "Gürsar Göktürk Okulu Sebahattin'e 10.000 TL ödeme kaydet",
                "gursar_mimarlik",
            )
        self.assertEqual(hazir["dogrulama"]["durum"], "FAIL")
        self.assertIn("1. kayıt: paranın çıktığı/girdiği hesap veya kasa", ui._eksik_alanlari_bul(hazir))

    def test_eksik_tutar_fail_olur_ve_onaylanamaz(self):
        ai = {"kayitlar": [{
            "Tarih": "2026-10-02", "Cari": "İşveren", "Islem_Turu": "hakediş", "PB": "TL",
        }]}
        with patch.object(sk, "master_data_oku", return_value=MD), \
             patch.object(sk, "metne_sor", return_value=ai), patch.object(sk, "audit_yaz"):
            hazir = sk.taslak_hazirla("Gürsar için hakediş ekle", "gursar_mimarlik")
        self.assertEqual(hazir["dogrulama"]["durum"], "FAIL")
        self.assertTrue(any("Tutar" in x for x in hazir["dogrulama"]["hatalar"]))

    def test_taslak_onaydan_once_yazmaz_onayda_yazar(self):
        hazir = {
            "basari": True, "foto_yolu": None, "firma_key": "gursar_mimarlik",
            "tur": "sohbet_kaydi", "belge_id": "CHAT-1", "cikarim_motoru": "metin_llm",
            "dogrulama": {"durum": "PASS", "skor": 100, "uyarilar": [], "hatalar": []},
            "veri": {"_hedef": "ana_ledger", "kayitlar": [{
                "Tarih": "2026-10-02", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
                "Cari_Tipi": "Usta", "Cari": "Sebahattin Çakmak",
                "Islem_Turu": "Usta/Tedarikçi Ödemesi", "Hesap_Kasa": "Gürsar TL Banka",
                "Tutar": 10000, "PB": "TL", "Kur": 1,
            }]},
        }
        with tempfile.TemporaryDirectory() as d, patch.object(ui, "STATE_PATH", Path(d) / "state.json"), \
             patch.object(ui, "taslak_hazirla", return_value=hazir), \
             patch.object(ui, "hazirlanan_belgeyi_yaz", return_value=(True, "")) as yaz, \
             patch.object(ui, "_onay_parolasini_dogrula"), \
             patch.object(ui, "onay_kararlari_temizle", return_value=0), \
             patch.object(ui, "audit_yaz"):
            taslak = ui.sohbet_taslagi_hazirla("ödeme kaydet", "gursar_mimarlik", "test")
            yaz.assert_not_called()
            sonuc = ui.onayla(taslak["token"], "test", "test-parolasi")
        self.assertTrue(sonuc["tamam"])
        yaz.assert_called_once()

    def test_taslak_duzeltme_onceki_onayi_sifirlar(self):
        eski = {
            "firma_key": "gursar_mimarlik", "belge_id": "ESKI-1",
            "veri": {"kayitlar": [{"Tutar": 1}]},
        }
        yeni = {
            **eski, "belge_id": "YENI-1",
            "dogrulama": {"durum": "REVIEW", "skor": 84, "uyarilar": [], "hatalar": []},
            "veri": {"kayitlar": [{"Tutar": 2}]},
        }
        ui._BEKLEYEN["tok"] = {
            "token": "tok", "durum": "ONAY_BEKLIYOR", "kaynak_turu": "sohbet",
            "olusturma": "2026-10-06T10:00:00", "aktor": "hazirlayan",
            "hazir": eski, "onay": {"mevcut": 1, "gerekli": 2},
        }
        with tempfile.TemporaryDirectory() as d, patch.object(ui, "STATE_PATH", Path(d) / "state.json"), \
             patch.object(ui, "taslak_hazirla", return_value=yeni), \
             patch.object(ui, "onay_kararlari_temizle", return_value=1) as temizle, \
             patch.object(ui, "audit_yaz"):
            sonuc = ui.sohbet_taslagi_duzelt("tok", "tutarı düzelt", "duzelten")
        temizle.assert_called_once_with("ESKI-1", "duzelten", "sohbet_taslagi_duzeltildi")
        self.assertIsNone(sonuc["onay"])

    def test_coklu_islem_yetersiz_ilk_cevapta_yedek_modele_gecer(self):
        ilk = {"kayitlar": [{
            "Tarih": "2026-10-04", "Cari": "Sebahattin Çakmak",
            "Cari_Tipi": "Usta", "Islem_Turu": "Usta/Tedarikçi Ödemesi",
            "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 32000, "PB": "TL", "Kur": 1,
        }]}
        yedek = {"kayitlar": [
            {"Tarih": "2026-10-04", "Cari": "Cemre Şensoy", "Cari_Tipi": "Diğer",
             "Islem_Turu": "Kredi Kullanımı", "Hesap_Kasa": "Gürsar USD Banka",
             "Tutar": 2275, "PB": "USD", "Kur": 48.43},
            {"Tarih": "2026-10-04", "Cari": "Cemre Şensoy", "Cari_Tipi": "Diğer",
             "Islem_Turu": "Kredi Kullanımı", "Hesap_Kasa": "Gürsar TL Banka",
             "Tutar": "32 bin TL", "PB": "TL", "Kur": 1},
            {"Tarih": "2026-10-05", "Cari": "Tuana Battal", "Cari_Tipi": "Usta",
             "Islem_Turu": "Gider Tahakkuku", "Tutar": "157.000 TL", "PB": "TL", "Kur": 1,
             "Vade": "2026-10-05"},
        ], "uyarilar": ["300.000 TL çek riski gerçek nakit hareketi değildir."]}
        metin = (
            "Gürsar Mimarlık Göktürk Okulu 300 bin TL çek Cemre Şensoy'a verildi; "
            "karşılığında 2275 USD ve 32 bin TL nakit alındı. 157 bin TL Tuana Battal'a "
            "havale yapılacak, Excel'e işleyelim."
        )
        with patch.object(sk, "master_data_oku", return_value=MD), \
             patch.object(sk, "metne_sor", return_value=ilk), \
             patch.object(sk, "metne_sor_yedek", return_value=yedek) as ikinci, \
             patch.object(sk, "audit_yaz"):
            hazir = sk.taslak_hazirla(metin, "gursar_mimarlik")
        ikinci.assert_called_once()
        self.assertEqual(len(hazir["veri"]["kayitlar"]), 3)
        self.assertEqual(hazir["veri"]["kayitlar"][1]["Tutar"], 32000.0)
        self.assertTrue(hazir["veri"]["kayitlar"][0]["_yeni_cari"])
        self.assertEqual(hazir["dogrulama"]["durum"], "REVIEW")

    def test_kosullu_cek_riski_gercek_gider_yapilmaz(self):
        veri = {"kayitlar": [
            {"Tarih": "04.10.2026", "Cari": "Cemre Şensoy", "Islem_Turu": "Çek Alındı",
             "Tutar": 32000, "PB": "TL", "Aciklama": "Çek karşılığı nakit alındı."},
            {"Tarih": "05.10.2026", "Cari": "Tuana Battal", "Islem_Turu": "Gider Tahakkuku",
             "Tutar": 157000, "PB": "TL", "Aciklama": "Havale yapılacak."},
            {"Tarih": "10.12.2026", "Cari": "Cemre Şensoy", "Islem_Turu": "Gider Tahakkuku",
             "Tutar": 300000, "PB": "TL", "Aciklama": "Çek karşılıksız çıkma ihtimali için nakit hazırlanması."},
        ]}
        metin = "Çek karşılığında 32 bin TL nakit alındı, 157 bin TL havale yapılacak; karşılıksız çıkarsa 300 bin TL nakit hazırlanmalı."
        sonuc = sk._is_kurallarini_uygula(veri, metin, MD)
        self.assertEqual(len(sonuc["kayitlar"]), 2)
        self.assertEqual(sonuc["kayitlar"][0]["Islem_Turu"], "Çek Teminatlı Borçlanma")
        self.assertEqual(sonuc["kayitlar"][1]["Vade"], "05.10.2026")
        self.assertEqual(sonuc["riskler"][0]["Tutar"], 300000)
        self.assertIn("300000 TL nakit rezervi", sonuc["kayitlar"][0]["Aciklama"])
        self.assertTrue(any("gerçek hareket olarak yazılmadı" in x for x in sonuc["uyarilar"]))

    def test_ham_model_havale_ve_risk_ciktisi_guvenli_taslaga_donusur(self):
        ham = {"kayitlar": [
            {"Tarih": "04.10.2026", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Diğer", "Cari": "Cemre Şensoy", "Islem_Turu": "Çek Alındı",
             "Hesap_Kasa": "Gürsar TL Banka", "Tutar": "32 bin TL", "PB": "TL", "Kur": 1,
             "Aciklama": "Çek karşılığı nakit alındı."},
            {"Tarih": "04.10.2026", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Diğer", "Cari": "Cemre Şensoy", "Islem_Turu": "Çek Alındı",
             "Hesap_Kasa": "Gürsar USD Banka", "Tutar": 2275, "PB": "USD", "Kur": 48.43,
             "Aciklama": "Çek karşılığı nakit alındı."},
            {"Tarih": "05.10.2026", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Müşteri", "Cari": "Tuana Battal", "Islem_Turu": "Havale",
             "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 157000, "PB": "TL", "Kur": 1,
             "Aciklama": "Havale işlemi."},
            {"Tarih": "10.12.2026", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Diğer", "Cari": "Cemre Şensoy", "Islem_Turu": "Gider Tahakkuku",
             "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 300000, "PB": "TL", "Kur": 1,
             "Aciklama": "Çek karşılıksız çıkma ihtimali için nakit hazırlanması."},
        ]}
        metin = (
            "Gürsar Göktürk Okulu çek Cemre Şensoy'a verildi; karşılığında 2275 USD ve 32 bin TL "
            "nakit alındı. 05.10.2026 tarihinde 157 bin TL Tuana Battal'a havale yapılacak; "
            "karşılıksız çıkarsa 300 bin TL nakit hazırlanmalı."
        )
        veri, dogrulama = sk._adayi_dogrula(ham, metin, "gursar_mimarlik", MD)
        self.assertEqual(len(veri["kayitlar"]), 3)
        self.assertEqual(veri["kayitlar"][2]["Islem_Turu"], "Gider Tahakkuku")
        self.assertEqual(veri["kayitlar"][2]["Vade"], "05.10.2026")
        self.assertFalse(dogrulama["hatalar"])
        self.assertEqual(dogrulama["durum"], "REVIEW")

    def test_model_mevcut_cariyi_uydurursa_kullanici_adina_geri_doner(self):
        ham = {"kayitlar": [
            {"Tarih": "04.10.26", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Banka", "Cari": "Ziraat", "Islem_Turu": "Çek Alındı",
             "Hesap_Kasa": "Gürsar USD Banka", "Tutar": 2275, "PB": "USD", "Kur": 48.43,
             "Aciklama": "Çek Cemre Şensoya verildi."},
            {"Tarih": "04.10.26", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktyürk Okulu",
             "Cari_Tipi": "Banka", "Cari": "Ziraat", "Islem_Turu": "Nakit Girişi",
             "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 32000, "PB": "TL", "Kur": 1,
             "Aciklama": "32 bin TL nakit elden alındı."},
            {"Tarih": "05.10.26", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Diğer", "Cari": "Tuana Battal", "Islem_Turu": "Kredi Kullanımı",
             "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 157000, "PB": "TL", "Kur": 1,
             "Vade": "", "Aciklama": "Havale yapıldı. Çek teminatı karşılığı finansman girişi."},
        ]}
        metin = (
            "Çek Cemre Şensoya verildi. 04.10.26 tarihinde. Karşılığında 2275 USD ve 32 bin TL "
            "nakit alındı. 05.10.2026 tarihinde 157 bin TL Tuana Battal hesabına havale yapılacak. "
            "Excel'e işleyelim. Faaliyet: Gürsar Mimarlık"
        )
        veri, dogrulama = sk._adayi_dogrula(ham, metin, "gursar_mimarlik", MD)
        self.assertFalse(dogrulama["hatalar"])
        self.assertTrue(all(x["Proje"] == "Göktürk Okulu" for x in veri["kayitlar"]))
        self.assertEqual(veri["kayitlar"][0]["Cari"], "Cemre Şensoy")
        self.assertEqual(veri["kayitlar"][1]["Cari"], "Cemre Şensoy")
        self.assertEqual(veri["kayitlar"][0]["Islem_Turu"], "Çek Teminatlı Borçlanma")
        self.assertEqual(veri["kayitlar"][1]["Islem_Turu"], "Çek Teminatlı Borçlanma")
        self.assertEqual(veri["kayitlar"][2]["Cari"], "Tuana Battal")
        self.assertEqual(veri["kayitlar"][2]["Islem_Turu"], "Gider Tahakkuku")
        self.assertEqual(veri["kayitlar"][2]["Vade"], "05.10.26")
        self.assertNotIn("finansman girişi", veri["kayitlar"][2]["Aciklama"])

    def test_nesne_bicimindeki_risk_notu_temiz_metne_cevrilir(self):
        ham = {
            "kayitlar": [{
                "Tarih": "04.10.2026", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
                "Cari_Tipi": "Diğer", "Cari": "Cemre Şensoy", "Islem_Turu": "Kredi Kullanımı",
                "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 32000, "PB": "TL", "Kur": 1,
                "Aciklama": "Çek teminatı karşılığı finansman girişi.",
            }],
            "uyarilar": [{"mesaj": "10.12.2026 tarihinde 300.000 TL koşullu nakit rezervi gerekebilir."}],
        }
        veri, dogrulama = sk._adayi_dogrula(
            ham,
            "Gürsar için 32 bin TL kredi kullanımı kaydet; 10.12.2026 tarihinde 300 bin TL rezerv gerekebilir.",
            "gursar_mimarlik",
            MD,
        )
        self.assertEqual(veri["uyarilar"], ["10.12.2026 tarihinde 300.000 TL koşullu nakit rezervi gerekebilir."])
        self.assertTrue(any("AI kayıt notu: 10.12.2026" in x for x in dogrulama["uyarilar"]))
        self.assertFalse(any("{'mesaj'" in x for x in dogrulama["uyarilar"]))
        self.assertEqual(
            sk._uyari_metni("AI kayıt notu: {'mesaj': '300.000 TL koşullu rezerv.'}"),
            "300.000 TL koşullu rezerv.",
        )
        self.assertEqual(
            sk._uyari_metni("{'mesaj': '300.000 TL koşullu rezerv.'}"),
            "300.000 TL koşullu rezerv.",
        )


if __name__ == "__main__":
    unittest.main()
