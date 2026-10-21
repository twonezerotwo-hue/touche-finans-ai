"""Çoklu CFO işlem paketinin güvenli ve idempotent sözleşmesi."""
import hashlib
from unittest.mock import patch

import cfo_batch
import cfo_intelligence as intelligence
from cfo_test_temeli import AKTOR, BUGUN, CfoTestTemeli


class CfoBatchTests(CfoTestTemeli):
    def setUp(self):
        super().setUp()
        self.batch_path = self.root / "cfo_batchleri.json"
        self.path_patch = patch.object(cfo_batch, "_PATH", self.batch_path)
        self.path_patch.start()
        cfo_batch._BATCHES = {}

    def tearDown(self):
        cfo_batch._BATCHES = {}
        self.path_patch.stop()
        super().tearDown()

    def _multi(self):
        text = (
            "Gürsar İnşaat Göktürk Okulu'nda 12.10.2026 Devrim'e 10.000 TL elden ödeme yaptık; "
            "Gürsar İnşaat Göktürk Okulu'nda 12.10.2026 İşveren'e 20.000 TL elden ödeme yaptık; "
            "Gürsar İnşaat Göktürk Okulu'nda 12.10.2026 Sebahattin Çakmak'a 30.000 TL elden ödeme yaptık"
        )
        result = intelligence.analyze(text, self.katalog(), BUGUN)
        self.assertEqual(result.intent, intelligence.FINANCIAL_DRAFT_REQUEST, result.as_dict())
        self.assertFalse(result.clarification, result.as_dict())
        self.assertEqual(len(result.proposals), 3)
        return text, result

    def test_uc_bagimsiz_islem_tek_pakette_tutulur_ve_excel_degismez(self):
        text, result = self._multi()
        before = hashlib.sha256(self.ana.read_bytes()).hexdigest()
        paket = cfo_batch.create(result, text, AKTOR)
        self.assertEqual(len(paket["items"]), 3)
        self.assertTrue(all(x["status"] == "PENDING" for x in paket["items"]))
        self.assertEqual(before, hashlib.sha256(self.ana.read_bytes()).hexdigest())
        self.assertTrue(self.batch_path.exists())

    def test_ayni_mesaj_tekrarinda_ayni_batch_doner(self):
        text, result = self._multi()
        first = cfo_batch.create(result, text, AKTOR)
        second = cfo_batch.create(result, text, AKTOR)
        self.assertEqual(first["batch_id"], second["batch_id"])

    def test_ayni_degerli_iki_odeme_mukerrer_inceleme_ister(self):
        text = (
            "Gürsar İnşaat Göktürk Okulu'nda Devrim'e 10.000 TL ödeme yaptık; "
            "Gürsar İnşaat Göktürk Okulu'nda Devrim'e 10.000 TL ödeme yaptık"
        )
        result = intelligence.analyze(text, self.katalog(), BUGUN)
        paket = cfo_batch.create(result, text, AKTOR)
        self.assertEqual(paket["items"][1]["status"], "NEEDS_REVIEW")
        self.assertEqual(paket["items"][1]["duplicate_of"], "item-01")
        with self.assertRaises(ValueError):
            cfo_batch.prepare_item(paket["batch_id"], "item-02", AKTOR, self.katalog(), BUGUN)

    def test_satir_duzeltme_mukerrer_kontrolunu_yeniden_calistirir(self):
        text, result = self._multi()
        paket = cfo_batch.create(result, text, AKTOR)
        updated = cfo_batch.correct_item(
            paket["batch_id"], "item-02",
            "Gürsar İnşaat Göktürk Okulu'nda İşveren'e 21.000 TL ödeme yaptık",
            AKTOR, self.katalog(), BUGUN,
        )
        self.assertEqual(updated["status"], "PENDING")
        self.assertEqual(updated["proposal"]["amount"], "21000")

    def test_satir_hazirlama_mevcut_parolali_kuyruga_gider_ve_deftere_yazmaz(self):
        text, result = self._multi()
        paket = cfo_batch.create(result, text, AKTOR)
        before = len(self.ledger())
        item = cfo_batch.prepare_item(paket["batch_id"], "item-01", AKTOR, self.katalog(), BUGUN)
        self.assertEqual(item["status"], "DRAFT_READY")
        self.assertTrue(item["draft_id"])
        self.assertEqual(len(self.ledger()), before)
        self.assertEqual(cfo_batch.prepare_item(paket["batch_id"], "item-01", AKTOR)["draft_id"], item["draft_id"])

    def test_satir_reddi_finansal_yazim_yapmaz(self):
        text, result = self._multi()
        paket = cfo_batch.create(result, text, AKTOR)
        before = len(self.ledger())
        rejected = cfo_batch.reject_item(paket["batch_id"], "item-03", AKTOR)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(len(self.ledger()), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
