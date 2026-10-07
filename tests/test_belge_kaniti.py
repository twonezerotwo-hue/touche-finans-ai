# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dogrulayici
import main


class BelgeKanitiTestleri(unittest.TestCase):
    def test_gorselde_olmayan_uydurma_alanlar_temizlenir(self):
        veri = {"kayitlar": [{
            "tarih": "01.01.2023", "makbuz_no": "12345", "odeyen": "Yönetici",
            "alan": "Araba Satışı", "tutar": "67000 TL", "aciklama": "Araba Satış Faturası",
            "odeme_sekli": "EFT",
        }]}
        ham = "2275 $\n32000 TL\n+ 30000 TL EFT\n67000 TL EFT"
        with patch.object(dogrulayici, "master_data_oku", return_value={}):
            sonuc = main._kanitli_dogrula(veri, "touche_holding", "makbuz", ham)
        kayit = veri["kayitlar"][0]
        self.assertEqual(kayit["Tarih"], "")
        self.assertEqual(kayit["Makbuz_No"], "")
        self.assertEqual(kayit["Odeyen"], "")
        self.assertEqual(kayit["Tutar"], 67000.0)
        self.assertEqual(kayit["odeme_sekli"], "EFT")
        self.assertEqual(sonuc["durum"], "REVIEW")
        self.assertTrue(any("bağımsız kanıt" in x for x in sonuc["uyarilar"]))


if __name__ == "__main__":
    unittest.main()
