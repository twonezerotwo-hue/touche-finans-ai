# -*- coding: utf-8 -*-
"""Regresyon: test izolasyon korumasi Windows'ta yanlis alarm vermemeli.

Windows'ta ``tempfile`` kullanici profilinin altindadir (``C:\\Users\\<ad>\\AppData\\Local\\Temp``).
Eski koruma yolda kullanici adini aradigi icin, dogru izole edilmis test oturumunu da reddediyordu
ve tum paket calismiyordu. Koruma yine de uretim OneDrive agacini reddetmelidir.
"""
import tempfile
import unittest
from pathlib import Path

from _ortam import yol_izolasyon_hatasi


class TestIzolasyonKorumasi(unittest.TestCase):
    def test_kullanici_profili_altindaki_gecici_kok_kabul_edilir(self):
        kok = Path(tempfile.mkdtemp(prefix="touche_test_"))
        try:
            ana = kok / "veri" / "TEST Touche Holding.xlsx"
            # Kullanici adi yolda gecse bile (ornegin .../Users/twone/AppData/...) izole kok kabul edilir.
            self.assertIsNone(yol_izolasyon_hatasi(ana, kok, onedrive=Path.home() / "OneDrive"))
        finally:
            kok.rmdir()

    def test_kok_disindaki_yol_reddedilir(self):
        kok = Path(tempfile.mkdtemp(prefix="touche_test_"))
        try:
            uretim = Path.home() / "OneDrive" / "Desktop" / "Touche" / "Touche Holding.xlsx"
            self.assertIn("uretim Excel yoluna", yol_izolasyon_hatasi(uretim, kok) or "")
        finally:
            kok.rmdir()

    def test_onedrive_icindeki_kok_reddedilir(self):
        taban = Path(tempfile.mkdtemp(prefix="touche_onedrive_"))
        try:
            kok = taban / "OneDrive" / "touche_test_x"
            kok.mkdir(parents=True)
            ana = kok / "veri" / "TEST.xlsx"
            self.assertIn("OneDrive", yol_izolasyon_hatasi(ana, kok, onedrive=taban / "OneDrive") or "")
        finally:
            import shutil
            shutil.rmtree(taban, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
