from __future__ import annotations

import json
from pathlib import Path

import cfo_evaluation as evaluation
import cfo_intelligence as intelligence
from cfo_test_temeli import BUGUN, CfoTestTemeli


class PremiumCfoEvaluation(CfoTestTemeli):
    def test_corpus_is_reproducible_split_and_leakage_free(self):
        first = evaluation.build_corpus(3000)
        second = evaluation.build_corpus(3000)
        assert first == second
        assert evaluation.corpus_digest(first) == evaluation.corpus_digest(second)
        assert evaluation.split_counts(first) == {"train": 1001, "validation": 1001, "holdout": 998}
        assert evaluation.leakage_violations(first) == []

    def test_holdout_metrics_measure_deterministic_intent_and_write_safety(self):
        corpus = evaluation.build_corpus(3000)
        result = evaluation.evaluate(
            corpus,
            lambda text: intelligence.analyze(text, self.katalog(), BUGUN),
            holdout_only=True,
        )
        assert result.count == 998
        assert result.intent_accuracy == 1.0
        assert result.amount_accuracy == 1.0
        assert result.currency_accuracy == 1.0
        assert result.read_write_safety_violations == 0
        assert result.leakage_violations == 0

    def test_manifest_is_versioned_and_does_not_claim_live_model_accuracy(self):
        manifest = json.loads((Path(__file__).parent / "fixtures" / "cfo_eval_manifest.v1.json").read_text(encoding="utf-8"))
        assert manifest["version"] == "cfo-eval-v1"
        assert manifest["generator"] == "scripts/cfo_evaluation.py"
        assert manifest["live_model_evaluation"] == "NOT_RUN"
        assert manifest["production_validation"] == "NOT_VERIFIED"

