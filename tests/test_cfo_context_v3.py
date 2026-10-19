"""PR-18: açık Türkçe bağlam kullanımı ve sessiz miras alma koruması."""
import unittest

import cfo_intelligence as I
from cfo_test_temeli import BUGUN, CfoTestTemeli


class CfoContextV3Tests(CfoTestTemeli):
    def test_baglam_yoksa_takip_mesaji_onceki_faaliyeti_miras_almaz(self):
        result = I.analyze_turn(
            "20 bin TL ödeme yaptık", self.katalog(), BUGUN,
            I.ConversationContext(activity="Gürsar Mimarlık", project="Göktürk Okulu"),
        )
        self.assertFalse(result.safe_to_draft)
        self.assertIn("Faaliyet", result.clarification)
        self.assertEqual(result.context_used, ())

    def test_acik_ayni_proje_gonderimi_baglam_kullanir(self):
        result = I.analyze_turn(
            "aynı projede Devrim'e 20 bin TL ödeme yaptık", self.katalog(), BUGUN,
            I.ConversationContext(activity="Gürsar Mimarlık", project="Göktürk Okulu"),
        )
        self.assertEqual(result.intent, I.FINANCIAL_DRAFT_REQUEST)
        self.assertTrue(result.safe_to_draft)
        self.assertEqual(result.proposals[0].activity, "Gürsar Mimarlık")
        self.assertEqual(result.proposals[0].project, "Göktürk Okulu")
        self.assertEqual(result.context_used, ("project",))

    def test_acik_baglam_karsit_proje_sessizce_degistirilmez(self):
        result = I.analyze_turn(
            "Kavacık Projesi'nde Devrim'e 20 bin TL ödeme yaptık",
            self.katalog(), BUGUN,
            I.ConversationContext(activity="Gürsar Mimarlık", project="Göktürk Okulu"),
        )
        self.assertEqual(result.context_used, ())
        self.assertNotEqual(result.proposals[0].project, "Göktürk Okulu")

    def test_baglamdan_taslak_sonrasi_context_uretilir(self):
        result = I.analyze(
            "Gürsar Mimarlık Göktürk Okulu'nda Devrim'e 10 bin TL ödeme yaptık",
            self.katalog(), BUGUN,
        )
        context = I.context_from_result(result)
        self.assertEqual(context.as_dict(), {
            "activity": "Gürsar Mimarlık",
            "project": "Göktürk Okulu",
            "counterparty": "Devrim",
            "currency": "TL",
        })

    def test_eksik_alanlar_uygulanabilir_soruya_cevrilir(self):
        result = I.analyze("Devrim'e 10 bin TL ödeme yaptık", self.katalog(), BUGUN)
        questions = I.clarification_questions(result)
        self.assertTrue(any("Faaliyet" in item for item in questions))
        self.assertTrue(all(item.endswith((".", ")")) for item in questions))


if __name__ == "__main__":
    unittest.main()
