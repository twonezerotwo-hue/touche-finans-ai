"""Sürüm kontrollü sentetik Türkçe CFO komut sözleşmesi."""
import json
import unittest
from pathlib import Path

import cfo_intelligence as I
from cfo_test_temeli import BUGUN, CfoTestTemeli


class CfoCommandCorpusTests(CfoTestTemeli):
    def test_v1_korpusunun_intentleri(self):
        yol = Path(__file__).parent / "fixtures" / "cfo_turkish_commands.v1.json"
        vakalar = json.loads(yol.read_text(encoding="utf-8"))
        for vaka in vakalar:
            with self.subTest(text=vaka["text"]):
                sonuc = I.analyze(vaka["text"], self.katalog(), BUGUN)
                self.assertEqual(sonuc.intent, vaka["intent"])
                if sonuc.intent != I.FINANCIAL_DRAFT_REQUEST:
                    self.assertFalse(sonuc.safe_to_draft)


if __name__ == "__main__":
    unittest.main()
