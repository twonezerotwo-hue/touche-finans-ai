# -*- coding: utf-8 -*-
"""Islem turu -> muhasebe hesap eslemesi: odeme gider degildir, grup ici transfer gider degildir."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import finans_cekirdegi as c


def esle(islem, hesap="Gürsar TL Banka"):
    return c._hesap_eslesmesi({"Islem_Turu": islem, "Hesap_Kasa": hesap}, "finans_belgesi")


class MuhasebeEslemesiTestleri(unittest.TestCase):
    def test_odeme_gider_degil_borc_kapatmadir(self):
        self.assertEqual(esle("Usta/Tedarikçi Ödemesi"), ("320", "102"))     # Satıcılar BORÇ / Banka ALACAK
        self.assertEqual(esle("Usta/Tedarikçi Ödemesi", "Gürsar TL Kasa"), ("320", "100"))

    def test_gider_tahakkuku_maliyet_ve_borc_dogurur_nakit_cikmaz(self):
        self.assertEqual(esle("Gider Tahakkuku"), ("740", "320"))

    def test_tahsilat_alacagi_kapatir_gelir_degildir(self):
        self.assertEqual(esle("Tahsilat"), ("102", "120"))
        self.assertEqual(esle("Hakediş / Gelir Tahakkuku"), ("120", "600"))

    def test_kredi_kullanimi_gelir_degil_borctur(self):
        self.assertEqual(esle("Kredi Kullanımı"), ("102", "300"))

    def test_grup_ici_transfer_gidere_yazilamaz(self):
        for ad in ("Intercompany Transfer", "Grup İçi Transfer", "Şirketler Arası Transfer"):
            with self.subTest(ad=ad), self.assertRaisesRegex(ValueError, "Grup içi transfer"):
                esle(ad)

    def test_bilinmeyen_tur_sessizce_gidere_yazilmaz(self):
        for ad in ("Teminat Mektubu", "Tanımsız Hareket"):
            with self.subTest(ad=ad), self.assertRaisesRegex(ValueError, "eşlemesi tanımlı değil"):
                esle(ad)


if __name__ == "__main__":
    unittest.main()
