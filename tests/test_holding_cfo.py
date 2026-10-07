# -*- coding: utf-8 -*-
"""CFO Operating System katmani icin finansal unit testleri ve invariantlar."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finans_cekirdegi as cekirdek
import guvenlik
import kurumsal_finans as kurumsal
import money
import veri_katmani
import finans_motoru
import nakit_13hafta
import ticari
import senaryo_motoru
import belge_pipeline
import fx_provider
import yetki
import fpa_ileri


class HoldingCfoTestleri(unittest.TestCase):
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
        veri_katmani._HAZIRLANAN.clear()
        veri_katmani.veritabani_hazirla()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def _fis(self, belge, tarih, islem, tutar, cari, proje="Gokturk Okulu", vade=None, pb="TL", kur=1):
        kayit = {"Tarih": tarih, "Islem_Turu": islem, "Tutar": tutar, "PB": pb, "Kur": kur,
                 "Cari": cari, "Proje": proje}
        if vade:
            kayit["Vade"] = vade
        cekirdek.finans_kaydi_yaz({"belge_id": belge, "firma_key": "gursar_mimarlik",
                                  "tur": "finans_belgesi", "veri": {"kayitlar": [kayit]}}, "test")

    def _nakit(self, tutar=900_000):
        cekirdek.acilis_bakiyesi_kaydet("gursar_mimarlik", "102", "2026-01-01", tutar, "test")

    # --- Money / decimal ---
    def test_money_kayan_nokta_hatasi_yok(self):
        toplam = money.Money.from_tutar("0.10") + money.Money.from_tutar("0.20")
        self.assertEqual(toplam.bicim(kusuratsiz=False), "\u20ba0,30")
        self.assertEqual(money.kurus("123.456"), 12346)

    def test_tl_ve_yuzde_bicimi(self):
        self.assertEqual(money.tl_metin(1_250_000), "\u20ba1.250.000")
        self.assertEqual(money.yuzde_metin(24.5), "%24,5")

    # --- Karlilik invariantlari ---
    def test_brut_kar_ve_marj_invariantlari(self):
        kar = finans_motoru.karlilik(500_000, 300_000)
        self.assertEqual(kar["brut_kar_kurus"], 200_000)   # GP = Rev - Cost
        self.assertEqual(kar["brut_marj_yuzde"], 40.0)     # GM = GP / Rev

    def test_gelir_sifir_marj_na(self):
        kar = finans_motoru.karlilik(0, 100)
        self.assertIsNone(kar["brut_marj_yuzde"])          # %0 degil, N/A
        self.assertEqual(finans_motoru.marj_riski(None), "na")
        self.assertEqual(finans_motoru.marj_metin(None), "\u2014")

    def test_negatif_maliyet_danger(self):
        self.assertEqual(finans_motoru.marj_riski(-5), "danger")

    # --- Nakit / yatirim kapasitesi ---
    def test_nakit_ve_kullanilabilir_nakit(self):
        self._nakit(900_000)
        kpi = finans_motoru.holding_kpi()
        self.assertEqual(kpi["toplam_nakit_kurus"], 90_000_000)
        self.assertEqual(kpi["kullanilabilir_nakit_kurus"] + kpi["korunacak_nakit_kurus"],
                         kpi["toplam_nakit_kurus"])  # kullanilabilir = nakit - korunacak

    # --- AR/AP yaslandirma ---
    def test_alacak_gecikmis_isaretlenir(self):
        self._fis("AR1", "2026-01-05", "Hakedis", 500_000, "Musteri A", vade="2026-01-10")
        rapor = ticari.alacaklar(rapor_tarihi="2026-03-01")
        self.assertEqual(rapor["toplam"], 500_000.0)
        self.assertGreater(rapor["gecikmis"], 0)
        self.assertTrue(rapor["kalemler"][0]["gecikmis"])

    def test_borc_yaslandirma(self):
        self._fis("AP1", "2026-01-05", "Gider Tahakkuk", 300_000, "Tedarikci X", vade="2026-02-01")
        rapor = ticari.borclar(rapor_tarihi="2026-02-15")
        self.assertEqual(rapor["toplam"], 300_000.0)

    # --- Double-entry + intercompany ---
    def test_cift_kayit_dengeli(self):
        self._fis("T1", "2026-01-05", "Hakedis", 500_000, "Musteri A")
        with cekirdek._baglanti() as con:
            dengesiz = con.execute(
                "SELECT COUNT(*) n FROM (SELECT fis_id FROM fis_satirlari GROUP BY fis_id "
                "HAVING SUM(borc_kurus)<>SUM(alacak_kurus))").fetchone()["n"]
        self.assertEqual(dengesiz, 0)  # debit toplam = credit toplam

    def test_sirketler_arasi_transfer_iki_tarafli(self):
        sonuc = kurumsal.sirketler_arasi_transfer("gursar_mimarlik", "touche_holding",
                                                  "2026-02-01", 100_000, "Test transfer", "test")
        mutabakat = kurumsal.sirketler_arasi_mutabakat()
        self.assertTrue(any(x["islem_id"] == sonuc["islem_id"] for x in mutabakat))

    def test_butce_sapmasi(self):
        self._fis("B1", "2026-03-05", "Gider Tahakkuk", 120_000, "Tedarikci X")
        with cekirdek._baglanti() as con:
            con.execute("INSERT INTO butceler(faaliyet_key,proje,hesap_kodu,yil,ay,tutar_kurus,guncelleme) "
                        "VALUES('gursar_mimarlik','Gokturk Okulu','740',2026,3,10000000,'test')")
        rapor = kurumsal.butce_sapma_raporu("gursar_mimarlik", "2026-03-01", "2026-03-31")
        self.assertGreaterEqual(rapor["toplam_gerceklesen"], 100_000)

    # --- 13 hafta invariant ---
    def test_13_hafta_kapanis_invariant(self):
        self._nakit(900_000)
        self._fis("C1", "2026-01-05", "Hakedis", 500_000, "Musteri A", vade="2026-01-20")
        self._fis("C2", "2026-01-06", "Gider Tahakkuk", 300_000, "Tedarikci X", vade="2026-01-25")
        analiz = nakit_13hafta.nakit_13_hafta(bugun=date(2026, 1, 10))
        onceki = analiz["baslangic_nakit_kurus"]
        for h in analiz["haftalar"]:
            giris = h["tahsilat_kurus"] + h["diger_giris_kurus"]
            cikis = (h["tedarikci_kurus"] + h["personel_kurus"] + h["vergi_kurus"] +
                     h["isletme_gider_kurus"] + h["yatirim_kurus"] + h["finansman_kurus"])
            self.assertEqual(h["kapanis_kurus"], onceki + giris - cikis)
            onceki = h["kapanis_kurus"]

    # --- Senaryo 4 seviye ---
    def test_senaryo_uygulanamaz_asiri_yatirim(self):
        self._nakit(100_000)
        s = senaryo_motoru.senaryo_hesapla(degisiklikler={"yeni_yatirim": 5_000_000})
        self.assertEqual(s["seviye"], "Uygulanamaz")
        self.assertEqual(s["risk"], "danger")

    def test_senaryo_kaydet_ve_karsilastir(self):
        self._nakit(900_000)
        senaryo_motoru.senaryo_kaydet("Base", "BASE")
        senaryo_motoru.senaryo_kaydet("Worst", "WORST", degisiklikler={"maliyet_artisi": 20})
        karsilastir = senaryo_motoru.senaryo_karsilastir()
        self.assertEqual(karsilastir["adet"], 2)

    # --- FX ---
    def test_fx_cevrim(self):
        kurumsal.kur_kaydet("2026-01-01", "USD", 32.5, "test")
        sonuc = fx_provider.kur_cevir(100, "USD", "TL")
        self.assertEqual(sonuc["tutar"], 3250.0)

    def test_fx_kur_yok_hata(self):
        with self.assertRaises(ValueError):
            fx_provider.kur_cevir(100, "EUR", "TL")

    # --- Belge pipeline / duplicate ---
    def test_belge_siniflandirma_ve_duplicate(self):
        p = belge_pipeline.pipeline_calistir("Fatura No: ABC123 Tutar 1.250,00 TL", "fatura.pdf")
        self.assertEqual(p["tur"], "invoice")
        ozet = p["icerik_hash"]
        with cekirdek._baglanti() as con:
            con.execute("INSERT INTO belge_butunlugu(belge_id,sha256,kayit_zamani) VALUES('B1',?, 'x')",
                        (ozet,))
        self.assertTrue(belge_pipeline.mukerrer_kontrol(ozet)["mukerrer"])
        self.assertFalse(belge_pipeline.mukerrer_kontrol(belge_pipeline.icerik_hash(metin="yeni"))["mukerrer"])

    # --- Yetki ---
    def test_yetki_sinirlari(self):
        self.assertFalse(yetki.izin_var("VIEWER", "finans.duzenle"))
        self.assertTrue(yetki.izin_var("CFO", "donem.kapat"))
        self.assertTrue(yetki.izin_var("ADMIN", "ayar.duzenle"))  # legacy esleme
        with self.assertRaises(PermissionError):
            yetki.kontrol("VIEWER", "transfer.yap")

    # --- FP&A ---
    def test_fpa_mape_ve_duyarlilik(self):
        self.assertEqual(fpa_ileri.mape([100, 200], [100, 200]), 0.0)
        self.assertGreater(fpa_ileri.mape([100, 200], [110, 180]), 0)
        self._fis("F1", "2026-01-05", "Hakedis", 500_000, "Musteri A")
        self._fis("F2", "2026-01-06", "Gider Tahakkuk", 300_000, "Tedarikci X")
        d = fpa_ileri.duyarlilik_analizi()
        self.assertEqual(len(d["maliyet_duyarliligi"]), 4)
        self.assertEqual(fpa_ileri.monte_carlo(iterasyon=200, tohum=7)["iterasyon"], 200)


if __name__ == "__main__":
    unittest.main()



