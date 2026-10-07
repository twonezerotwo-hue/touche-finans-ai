# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dogrulayici as d


class ParaNormalizasyonTestleri(unittest.TestCase):
    def test_turkce_ve_uluslararasi_tutarlar(self):
        self.assertEqual(d._tutar_cevir("67.000 TL"), 67000)
        self.assertEqual(d._tutar_cevir("2.275,50 ₺"), d.Decimal("2275.50"))
        self.assertEqual(d._tutar_cevir("2,275.50 USD"), d.Decimal("2275.50"))
        self.assertEqual(d._tutar_cevir("300 bin TL"), 300000)
        self.assertEqual(d._tutar_cevir("2,5 milyon"), 2500000)

    def test_tutar_metninden_para_birimi_bulunur(self):
        self.assertEqual(d._para_birimi_bul("2.275 $"), "USD")
        self.assertEqual(d._para_birimi_bul("67.000 TL"), "TL")
        self.assertEqual(d._para_birimi_bul("100 EUR"), "EUR")

    def test_makbuz_tutari_para_yazisiyla_fail_olmaz(self):
        veri = {"kayitlar": [{
            "Tarih": "2026-10-04", "Makbuz_No": "M-1", "Odeyen": "Test",
            "Tutar": "67.000 TL",
        }]}
        sonuc = d.deterministik_dogrula(veri, "touche_holding", "makbuz", md={})
        self.assertNotEqual(sonuc["durum"], d.FAIL)
        self.assertEqual(sonuc["veri"]["kayitlar"][0]["Tutar"], 67000.0)
        self.assertEqual(sonuc["veri"]["kayitlar"][0]["PB"], "TL")


if __name__ == "__main__":
    unittest.main()
