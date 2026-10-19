"""PR-13: aciklanabilir metrik ve kanit sozlesmesi icin sentetik testler."""
import finans_cekirdegi as cekirdek
import finans_provenance as prov
from cfo_test_temeli import CfoTestTemeli


class ProvenanceTestleri(CfoTestTemeli):
    def _fis(self, belge, tarih, islem, tutar, cari, proje="Göktürk Okulu", faaliyet="gursar_mimarlik", pb="TL", kur=1):
        kayit = {"Tarih": tarih, "Islem_Turu": islem, "Tutar": tutar, "PB": pb, "Kur": kur,
                 "Cari": cari, "Proje": proje, "Hesap_Kasa": "Gürsar TL Banka", "Aciklama": f"{islem} {cari}"}
        cekirdek.finans_kaydi_yaz({"belge_id": belge, "firma_key": faaliyet, "tur": "finans_belgesi",
                                   "veri": {"kayitlar": [kayit]}}, "test")

    def setUp(self):
        super().setUp()
        self._fis("R-1", "2026-09-01", "Hakediş / Gelir Tahakkuku", 500_000, "İşveren")
        self._fis("R-2", "2026-09-10", "Tahsilat", 200_000, "İşveren")
        self._fis("R-3", "2026-09-15", "Gider Tahakkuku", 300_000, "Devrim")
        self._fis("R-4", "2026-09-20", "Usta/Tedarikçi Ödemesi", 100_000, "Devrim")
        self._fis("R-5", "2026-10-02", "Usta/Tedarikçi Ödemesi", 40_000, "Sebahattin Çakmak")
        self._fis("R-6", "2026-10-03", "Usta/Tedarikçi Ödemesi", 2_275, "Cemre Şensoy", pb="USD", kur=35)

    def _fisler(self):
        with cekirdek._baglanti() as con:
            fisler, _ = __import__("proje_finansi")._satirlari_oku(con, None)
        return fisler

    def test_totals_formula_and_source_refs(self):
        r = prov.metric_provenance("revenue", self._fisler(), faaliyet="gursar_mimarlik")
        self.assertEqual(r["metric_id"], "revenue")
        self.assertEqual(r["value_kurus"], 500_000 * 100)
        self.assertIn("Gelir =", r["formula"])
        self.assertEqual(r["reconciliation_status"], "CONSISTENT")
        self.assertEqual(r["source_transactions"][0]["voucher_id"], "JRN-R-1-001")
        self.assertEqual(r["source_transactions"][0]["excel_record_id"], "R-1")

    def test_pagination_is_deterministic_and_bounded(self):
        r = prov.metric_provenance("payments", self._fisler(), faaliyet="gursar_mimarlik", page=2, page_size=1)
        self.assertEqual(r["pagination"], {"page": 2, "page_size": 1, "total": 3, "pages": 3})
        self.assertEqual(len(r["source_transactions"]), 1)
        self.assertEqual(r["source_transactions"][0]["voucher_id"], "JRN-R-5-001")

    def test_scope_and_period_filters_never_leak_other_records(self):
        r = prov.metric_provenance("payments", self._fisler(), faaliyet="gursar_mimarlik",
                                   proje="Göktürk Okulu", bas="2026-10-01", bit="2026-10-31")
        self.assertEqual(r["value_kurus"], 40_000 * 100 + 2_275 * 35 * 100)
        self.assertTrue(all(x["project"] == "Göktürk Okulu" for x in r["source_transactions"]))

    def test_currency_and_conversion_are_explicit(self):
        r = prov.metric_provenance("payments", self._fisler(), faaliyet="gursar_mimarlik")
        usd = next(x for x in r["source_transactions"] if x["currency"] == "USD")
        self.assertEqual(usd["conversion"]["target_currency"], "TL")
        self.assertEqual(usd["conversion"]["rate"], 35)
        self.assertTrue(usd["conversion"]["normalized"])

    def test_mismatch_is_visible_and_never_rewrites(self):
        r = prov.metric_provenance("expenses", self._fisler(), faaliyet="gursar_mimarlik",
                                   comparison={"sqlite_total_kurus": 1_000, "excel_total_kurus": 2_000})
        self.assertEqual(r["reconciliation_status"], "INCONSISTENT")
        self.assertEqual(r["reconciliation"]["difference_kurus"], 1_000)
        self.assertIn("otomatik düzeltme", r["warning"])

    def test_stale_and_no_evidence_states_are_not_presented_as_healthy(self):
        stale = prov.metric_provenance("payments", self._fisler(), freshness={"stale": True, "last_verified": "2020-01-01"})
        self.assertEqual(stale["reconciliation_status"], "STALE")
        empty = prov.metric_provenance("revenue", {}, faaliyet="gursar_mimarlik")
        self.assertEqual(empty["evidence"], "NO_EVIDENCE")
        self.assertEqual(empty["source_count"], 0)

    def test_unknown_metric_is_rejected_without_query_execution(self):
        with self.assertRaises(ValueError):
            prov.normalize_metric("select * from fisler")


if __name__ == "__main__":
    import unittest
    unittest.main()
