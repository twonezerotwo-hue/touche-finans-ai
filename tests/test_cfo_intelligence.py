"""PR-11 CFO sözleşmesi: sorgu/yazma ayrımı ve güvenli çoklu işlem."""
import unittest

import cfo_intelligence as I
import cfo_niyet as N
from cfo_test_temeli import BUGUN, CfoTestTemeli


class CfoIntelligenceContractTests(CfoTestTemeli):
    def test_soru_asla_finansal_yazma_intenti_olmaz(self):
        sonuc = I.analyze("Göktürk Okulu'nda Devrim'e toplam ne kadar ödeme yaptık?", self.katalog(), BUGUN)
        self.assertIn(sonuc.intent, {I.READ_ONLY_QUERY, I.REPORT_REQUEST})
        self.assertFalse(sonuc.safe_to_draft)
        self.assertEqual(sonuc.proposals, ())

    def test_tek_kayit_taslagi_yapilandirilir(self):
        sonuc = I.analyze(
            "Gürsar Mimarlık Göktürk Okulu'nda Devrim'e 10 bin TL ödeme yaptık",
            self.katalog(), BUGUN,
        )
        self.assertEqual(sonuc.intent, I.FINANCIAL_DRAFT_REQUEST)
        self.assertTrue(sonuc.safe_to_draft)
        self.assertEqual(sonuc.proposals[0].counterparty, "Devrim")
        self.assertEqual(sonuc.proposals[0].amount, "10000")

    def test_coklu_islem_bagimsiz_taslaklara_ayrilir(self):
        sonuc = I.analyze(
            "Gürsar Mimarlık Göktürk Okulu'nda Devrim'e 50 bin TL ödeme yaptık; "
            "Sebahattin Çakmak'tan 30 bin TL tahsil ettik",
            self.katalog(), BUGUN,
        )
        self.assertEqual(sonuc.intent, I.FINANCIAL_DRAFT_REQUEST)
        self.assertEqual(len(sonuc.proposals), 2)
        self.assertEqual({p.counterparty for p in sonuc.proposals}, {"Devrim", "Sebahattin Çakmak"})
        self.assertFalse(sonuc.safe_to_draft)

    def test_eksik_bilgide_soru_uretilir(self):
        sonuc = I.analyze("Devrim'e 10 bin TL ödeme yaptık", self.katalog(), BUGUN)
        self.assertEqual(sonuc.intent, I.CLARIFICATION_REQUIRED)
        self.assertIn("Faaliyet", sonuc.clarification)
        self.assertFalse(sonuc.safe_to_draft)

    def test_desteklenmeyen_islem_guvenli_durur(self):
        n = N.analiz_et("Gürsar Mimarlık Göktürk Okulu'nda 10 bin TL transfer yap", self.katalog(), BUGUN)
        sonuc = I.analyze("Gürsar Mimarlık Göktürk Okulu'nda 10 bin TL transfer yap", self.katalog(), BUGUN)
        self.assertEqual(sonuc.intent, I.UNSUPPORTED_REQUEST)
        self.assertFalse(sonuc.safe_to_draft)
        self.assertTrue(n.tur in {"KAYIT", "BELIRSIZ"})

    def test_ondalik_virgul_islem_sinirini_bozmaz(self):
        sonuc = I.analyze(
            "Gürsar Mimarlık Göktürk Okulu'nda Devrim'e 10.500,50 TL ödeme yaptık",
            self.katalog(), BUGUN,
        )
        self.assertEqual(len(sonuc.proposals), 1)
        self.assertEqual(sonuc.proposals[0].amount, "10500.50")

    def test_cancellation_ve_correction_yazma_degil(self):
        for metin, beklenen in (
            ("Devrim'e yazılan 10 bin TL ödemeyi iptal et", I.CANCELLATION_REQUEST),
            ("Devrim'e yazılan ödemeyi düzelt", I.CORRECTION_REQUEST),
        ):
            with self.subTest(metin=metin):
                sonuc = I.analyze(metin, self.katalog(), BUGUN)
                self.assertEqual(sonuc.intent, beklenen)
                self.assertFalse(sonuc.safe_to_draft)


if __name__ == "__main__":
    unittest.main()
