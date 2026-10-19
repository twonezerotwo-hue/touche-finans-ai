"""PR-16 finansal doğruluk sözleşmeleri (yalnızca sentetik SQLite)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finans_cekirdegi as cekirdek
import finans_provenance as provenance
import nakit_13hafta


class FinansalDogrulukV2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "synthetic.db"
        self.db_patch = patch.object(cekirdek, "DB_PATH", self.db)
        self.db_patch.start()
        cekirdek.veritabanini_hazirla()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def _fis(self, belge, tarih, islem, tutar, *, vade, durum=None):
        cekirdek.finans_kaydi_yaz({
            "belge_id": belge,
            "firma_key": "gursar_mimarlik",
            "tur": "finans_belgesi",
            "veri": {"kayitlar": [{
                "Tarih": tarih, "Vade": vade, "Islem_Turu": islem,
                "Tutar": tutar, "PB": "TL", "Kur": 1,
                "Cari": "Sentetik Cari", "Proje": "Sentetik Proje",
            }]},
        }, "test")
        if durum:
            with cekirdek._baglanti() as con:
                con.execute("UPDATE fisler SET durum=? WHERE belge_id=?", (durum, belge))

    def test_eski_islem_tarihli_belge_vadesiyle_forecasta_girer(self):
        """Vade penceresi işlem tarihinden bağımsızdır."""
        self._fis("OLD-DUE", "2025-12-01", "Hakedis", 1000, vade="2026-01-20")
        analiz = nakit_13hafta.nakit_13_hafta(
            bugun=date(2026, 1, 10), baslangic_nakit=0, hafta_sayisi=3,
        )
        self.assertEqual(sum(h["tahsilat_kurus"] for h in analiz["haftalar"]), 100_000)

    def test_vade_penceresi_disindaki_kayit_girmez(self):
        self._fis("OUTSIDE", "2025-12-01", "Hakedis", 1000, vade="2026-04-20")
        analiz = nakit_13hafta.nakit_13_hafta(
            bugun=date(2026, 1, 10), baslangic_nakit=0, hafta_sayisi=3,
        )
        self.assertEqual(sum(h["tahsilat_kurus"] for h in analiz["haftalar"]), 0)

    def test_iptal_edilmis_vade_forecasta_girmez(self):
        self._fis("CANCELLED", "2025-12-01", "Hakedis", 1000,
                  vade="2026-01-20", durum="IPTAL")
        analiz = nakit_13hafta.nakit_13_hafta(
            bugun=date(2026, 1, 10), baslangic_nakit=0, hafta_sayisi=3,
        )
        self.assertEqual(sum(h["tahsilat_kurus"] for h in analiz["haftalar"]), 0)

    def test_llm_desteksiz_tutar_ve_kanit_kimligini_reddeder(self):
        evidence = {
            "toplam_kurus": 100_000,
            "source_transactions": [{"transaction_id": "JRN-SYN-001"}],
        }
        sonuc = provenance.validate_llm_output(
            "Toplam 2.000,00 TL; kaynak JRN-SYN-999.",
            ["Kaynak JRN-SYN-999"], evidence,
        )
        self.assertFalse(sonuc["valid"])
        self.assertIn("2.000,00 TL", sonuc["unsupported_amounts"])
        self.assertIn("JRN-SYN-999", sonuc["unsupported_ids"])

    def test_llm_yalnizca_yetkili_kanitla_gecer(self):
        evidence = {
            "toplam_kurus": 100_000,
            "source_transactions": [{"transaction_id": "JRN-SYN-001"}],
        }
        sonuc = provenance.validate_llm_output(
            "Toplam 1.000,00 TL; kaynak JRN-SYN-001.",
            ["Kaynak JRN-SYN-001"], evidence,
        )
        self.assertTrue(sonuc["valid"])


if __name__ == "__main__":
    unittest.main()
