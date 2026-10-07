# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ollama_client as oc


class MetinSaglayiciTestleri(unittest.TestCase):
    def test_evren_openai_uyumlu_cevabi_jsona_cevirir(self):
        yanit = Mock()
        yanit.raise_for_status.return_value = None
        yanit.json.return_value = {
            "choices": [{"message": {"content": '{"cevap":"tamam"}'}}]
        }
        with patch.object(oc, "EVREN_API_KEY", "gizli-test-anahtari"), \
             patch.object(oc.requests, "post", return_value=yanit) as post:
            sonuc = oc._evrene_metni_sor("test")
        self.assertEqual(sonuc["cevap"], "tamam")
        self.assertEqual(post.call_args.kwargs["headers"]["X-API-Key"], "gizli-test-anahtari")
        self.assertNotIn("gizli-test-anahtari", str(post.call_args.kwargs["json"]))

    def test_evren_hatasinda_yerel_ollamaya_duser(self):
        with patch.object(oc, "TEXT_LLM_PROVIDER", "evren"), \
             patch.object(oc, "EVREN_API_KEY", "gizli-test-anahtari"), \
             patch.object(oc, "_evrene_metni_sor", side_effect=RuntimeError("terms")), \
             patch.object(oc, "_ollamaya_metni_sor", return_value={"cevap": "yerel"}):
            sonuc = oc.metne_sor("test")
        self.assertEqual(sonuc["cevap"], "yerel")
        self.assertEqual(oc.son_metin_saglayici(), "ollama")

    def test_yedek_model_ilk_saglayiciyi_tekrar_cagirmaz(self):
        with patch.object(oc, "TEXT_LLM_PROVIDER", "evren"), \
             patch.object(oc, "EVREN_API_KEY", "gizli-test-anahtari"), \
             patch.object(oc, "_evrene_metni_sor") as evren, \
             patch.object(oc, "_ollamaya_metni_sor", return_value={"cevap": "yerel"}) as ollama:
            sonuc = oc.metne_sor_yedek("test", haric="evren")
        self.assertEqual(sonuc["cevap"], "yerel")
        evren.assert_not_called()
        ollama.assert_called_once()


if __name__ == "__main__":
    unittest.main()
