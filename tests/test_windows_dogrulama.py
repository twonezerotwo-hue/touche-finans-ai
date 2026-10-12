# -*- coding: utf-8 -*-
"""scripts/windows_dogrulama.py: Windows/OneDrive dogrulama aracinin kendisi (Linux'ta kilit adimlari atlanir)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config
import windows_dogrulama as wd


class WindowsDogrulamaAraciTestleri(unittest.TestCase):
    def test_arac_temiz_calisir_uretime_dokunmaz_ve_klasorlerini_siler(self):
        wd.SONUCLAR.clear()
        ana, yedek, temp = config.ana_excel_yolu(), config.yedek_excel_yolu(), config.TEMP_DIR
        with tempfile.TemporaryDirectory() as ust:
            kod = wd.main(["--klasor", ust])
            self.assertEqual(kod, 0, [x for x in wd.SONUCLAR if x[1] == "FAIL"])
            self.assertEqual(list(Path(ust).iterdir()), [])          # yalniz kendi olusturdugunu siler, hepsini siler
        durumlar = {ad: d for ad, d, _ in wd.SONUCLAR}
        kilit = [d for ad, d in durumlar.items() if "Excel acik" in ad]
        self.assertTrue(kilit)
        if os.name != "nt":
            self.assertEqual(set(kilit), {"SKIP"})
        # yapilandirma geri yuklendi
        self.assertEqual((config.ana_excel_yolu(), config.yedek_excel_yolu(), config.TEMP_DIR), (ana, yedek, temp))


if __name__ == "__main__":
    unittest.main()
