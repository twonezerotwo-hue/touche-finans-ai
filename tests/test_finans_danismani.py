# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finans_danismani as fd
from finans_cekirdegi import faaliyet_anahtari_bul


class FinansDanismaniTestleri(unittest.TestCase):
    def test_profil_sinirlari(self):
        p = fd.DanismanProfili.olustur({"risk": "bilinmez", "rezerv_gun": 1, "yatirim_vadesi_ay": 999})
        self.assertEqual(p.risk, "dusuk")
        self.assertEqual(p.rezerv_gun, 7)
        self.assertEqual(p.yatirim_vadesi_ay, 120)

    def test_yatirim_sepeti_toplami_kapasiteyi_gecmez(self):
        p = fd.DanismanProfili("orta", 30, 12)
        sepet = fd._yatirim_sepeti(100_000, p)
        self.assertAlmostEqual(sum(x["tutar"] for x in sepet), 100_000, places=2)
        self.assertEqual(sum(x["oran"] for x in sepet), 100)

    def test_llm_hatasi_deterministik_cevabi_engellemez(self):
        pano = {
            "durum": "likiditeyi_koru", "ana_karar": "Likit kal.", "uyarilar": [],
            "ozet": {"nakit": 10, "alacak": 20, "borc": 30, "gelir": 0, "gider": 0, "net": 0},
            "nakit_plani": {"yatirim_kapasitesi": 0, "rezerv": 100, "finansman_acigi": 90,
                             "30_gun_beklenen_tahsilat": 0, "30_gun_vadeli_odeme": 0, "gunluk_gider": 1},
            "odemeler": [], "yatirim_sepeti": [],
            "son_hareketler": [],
        }
        with tempfile.TemporaryDirectory() as d, patch.object(fd, "karar_panosu", return_value=pano), \
             patch.object(fd, "metne_sor", side_effect=RuntimeError("offline")), \
             patch.object(fd, "AUDIT_PATH", Path(d) / "audit.jsonl"):
            sonuc = fd.asistana_sor("Ne kadar yatirim yapabilirim?", llm_kullan=True)
        self.assertEqual(sonuc["llm"], "deterministik_yedek")
        self.assertIn("guvenli nakit", sonuc["cevap"])

    def test_gercek_defter_panosu_negatif_kapasite_uretmez(self):
        pano = fd.karar_panosu("tum", {"rezerv_gun": 30}, bugun=date(2026, 9, 27))
        self.assertGreaterEqual(pano["nakit_plani"]["yatirim_kapasitesi"], 0)
        self.assertGreaterEqual(pano["nakit_plani"]["finansman_acigi"], 0)
        self.assertIn("ana_karar", pano)

    def test_touche_holding_secimi_konsolide_veriyi_gosterir(self):
        tumu = fd.karar_panosu("tum", {"rezerv_gun": 30}, bugun=date(2026, 9, 27))
        holding = fd.karar_panosu("touche_holding", {"rezerv_gun": 30}, bugun=date(2026, 9, 27))
        self.assertEqual(holding["ozet"], tumu["ozet"])
        self.assertEqual(holding["nakit_plani"], tumu["nakit_plani"])
        self.assertEqual(holding["odemeler"], tumu["odemeler"])

    def test_yapilmis_odeme_niyeti_acik_borctan_ayrilir(self):
        self.assertEqual(fd._niyet("100000 TL taş işleri ödemesi yapılmış mı?"), "gecmis_odeme")

    def test_yapilmis_odeme_cevabi_defter_hareketini_kullanir(self):
        pano = {
            "ozet": {}, "nakit_plani": {}, "odemeler": [],
            "son_hareketler": [{"yon": "Ödeme", "tarih": "2026-09-16", "cari": "Sebahattin Çakmak", "tutar": 100000, "aciklama": "Mermer / taş işleri"}],
        }
        cevap = fd._deterministik_cevap(pano, "gecmis_odeme")
        self.assertIn("Sebahattin Çakmak", cevap)
        self.assertIn("100,000.00", cevap)

    def test_gursar_insaat_aliasi_gursar_mimarliga_eslenir(self):
        self.assertEqual(faaliyet_anahtari_bul("Gürsar İnşaat"), "gursar_mimarlik")

    def test_usta_odemesi_maliyet_grubuyla_eslenir(self):
        self.assertEqual(fd._borc_grubu("Sebahattin Çakmak", "Mermer / taş işleri"), "Taş işleri")
        self.assertEqual(fd._borc_grubu("Medet İme", "Saha betonu"), "Saha betonu")

    def test_excel_kredi_plani_odenen_tutari_duser(self):
        with tempfile.TemporaryDirectory() as d:
            yol = Path(d) / "master.xlsx"
            wb = Workbook()
            wb.remove(wb.active)
            ayarlar = wb.create_sheet("99_Ayarlar")
            ayarlar["AD3"], ayarlar["AE3"] = "TL", 1
            ayarlar["AD4"], ayarlar["AE4"] = "EUR", 55
            ledger = wb.create_sheet("03_Kayıt Merkezi")
            ledger["G6"], ledger["I6"], ledger["T6"] = "Kredi Taksit Ödemesi", 100, "KRD-001-01"
            ledger["V6"], ledger["AC6"] = "Gerçekleşti", "Onaylandı"
            plan = wb.create_sheet("04_Kredi & Vade")
            plan["A13"], plan["B13"], plan["C13"] = "KRD-001-01", "KRD-001", "Gürsar İnşaat"
            plan["D13"], plan["G13"], plan["J13"] = datetime(2026, 10, 20), 462.8, "EUR"
            wb.save(yol)
            wb.close()
            with patch.dict(fd.FIRMALAR["touche_holding"], {"excel": str(yol)}):
                taksitler = fd._kredi_taksitleri()
        self.assertEqual(len(taksitler), 1)
        self.assertEqual(taksitler[0]["taksit_id"], "KRD-001-01")
        self.assertAlmostEqual(taksitler[0]["tutar"], 19_954.00, places=2)

    def test_gercek_kredi_plani_vadeli_odeme_olarak_gorunur(self):
        pano = fd.karar_panosu("tum", {"rezerv_gun": 30}, bugun=date(2026, 9, 27))
        self.assertEqual(pano["odemeler"][0]["proje"], "KRD-001-01")
        self.assertEqual(pano["odemeler"][0]["vade"], "2026-10-20")
        self.assertAlmostEqual(pano["odemeler"][0]["tutar"], 25_755.79, places=2)
        self.assertAlmostEqual(pano["nakit_plani"]["30_gun_vadeli_odeme"], 25_755.79, places=2)

    def test_gelismis_pano_risk_projeksiyon_ve_veri_kalitesi_uretir(self):
        pano = fd.karar_panosu("tum", {"rezerv_gun": 30}, bugun=date(2026, 9, 27))
        self.assertEqual(len(pano["nakit_projeksiyonu"]), 13)
        self.assertIn("skor", pano["risk"])
        self.assertIn("skor", pano["veri_kalitesi"])
        self.assertTrue(pano["aksiyonlar"])

    def test_senaryo_yatirim_finansman_etkisini_hesaplar(self):
        sonuc = fd.senaryo_hesapla("tum", {"rezerv_gun": 30}, {
            "ek_tahsilat": 100_000, "ek_odeme": 25_000, "yeni_yatirim": 50_000,
        })
        self.assertAlmostEqual(sonuc["senaryo_nakit"], sonuc["mevcut_nakit"] + 25_000, places=2)
        self.assertGreaterEqual(sonuc["finansman_acigi"], 0)
        self.assertGreaterEqual(sonuc["yatirim_kapasitesi"], 0)

    def test_yeni_asistan_niyetleri(self):
        self.assertEqual(fd._niyet("Bugün hangi aksiyonları almalıyım?"), "aksiyon")
        self.assertEqual(fd._niyet("Eksik finans verileri neler?"), "veri_kalitesi")
        self.assertEqual(fd._niyet("13 haftalık nakit projeksiyonu"), "nakit")


if __name__ == "__main__":
    unittest.main()
