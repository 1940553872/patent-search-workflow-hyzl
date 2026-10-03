"""Formal unit acceptance of evaluation arithmetic and label provenance only.

The fixtures below are synthetic software test inputs, never real-task metrics.
"""

import copy
import unittest

from shared.evaluation import evaluate


class EvaluationAcceptance(unittest.TestCase):
    def setUp(self):
        self.evidence = {"query_id": "fixture-run", "snapshot": "2026-10-03",
                         "docs": [{"key": "FIXTURE-A", "class": "匹配",
                                   "document": {"sha256": "a" * 64},
                                   "evidence": [{"state": "有支持", "quote": "固定软件验收引文", "loc": "[0001]",
                                                 "page": 1, "value": "PASS", "issues": []}]}],
                         "scope": {"queries": [{"required": True, "status": "complete"}], "hard_excluded_keys": []},
                         "metrics": {"evidence_location_completeness": 1},
                         "counts": {"candidates": 1, "matched": 1, "excluded": 0, "pending": 0,
                                    "unclassified": 0, "hard_excluded_unclassified": 0}}
        self.labels = {"query_id": "fixture-run", "snapshot": "2026-10-03",
                       "items": [{"key": "FIXTURE-A", "expected_relevant": True, "match_correct": True}]}

    def test_complete_metrics_wilson_interval_and_no_input_mutation(self):
        original = copy.deepcopy(self.evidence)
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["precision"]["value"], 1)
        lo, hi = result["metrics"]["precision"]["wilson_interval"]
        self.assertAlmostEqual(lo, 0.2065493144, places=8)
        self.assertAlmostEqual(hi, 1, places=8)
        self.assertEqual(self.evidence, original)

    def test_missing_human_labels_and_zero_denominators_never_default_to_pass(self):
        self.labels["items"] = []
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        for metric in ("precision", "false_exclusion_rate"):
            self.assertIsNone(result["metrics"][metric]["value"])
            self.assertEqual(result["metrics"][metric]["assessment"], "未评测")
        self.assertIsNone(result["metrics"]["precision"]["wilson_interval"])
        self.evidence["docs"] = []
        self.evidence["scope"]["queries"] = []
        self.evidence["counts"].update(candidates=0, matched=0)
        self.evidence["metrics"]["evidence_location_completeness"] = None
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertIsNone(result["metrics"]["required_query_execution"]["value"])

    def test_rejects_mismatched_snapshot_unknown_duplicate_and_nonboolean_labels(self):
        variants = []
        for key in ("query_id", "snapshot"):
            variant = copy.deepcopy(self.labels)
            variant[key] = "wrong"
            variants.append(variant)
        variant = copy.deepcopy(self.labels)
        variant["items"][0]["key"] = "unknown"
        variants.append(variant)
        variant = copy.deepcopy(self.labels)
        variant["items"].append(copy.deepcopy(variant["items"][0]))
        variants.append(variant)
        variant = copy.deepcopy(self.labels)
        variant["items"][0]["match_correct"] = 1
        variants.append(variant)
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                evaluate(self.evidence, variant)

    def test_precision_target_includes_exact_85_percent(self):
        prototype = self.evidence["docs"][0]
        self.evidence["docs"] = [dict(prototype, key=f"FIXTURE-{i}") for i in range(20)]
        self.evidence["counts"].update(candidates=20, matched=20)
        self.labels["items"] = [{"key": f"FIXTURE-{i}", "match_correct": i < 17, "expected_relevant": True} for i in range(20)]
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["metrics"]["precision"]["value"], 0.85)
        self.assertEqual(result["status"], "PASS")
        self.labels["items"][16]["match_correct"] = False
        self.assertEqual(evaluate(self.evidence, self.labels)["status"], "FAIL")

    def test_false_exclusion_denominator_is_relevant_labels_and_nonmatches_do_not_affect_precision(self):
        self.evidence["docs"].append({"key": "FIXTURE-B", "class": "排除", "evidence": []})
        self.evidence["counts"].update(candidates=2, excluded=1)
        self.labels["items"].append({"key": "FIXTURE-B", "expected_relevant": True, "match_correct": False})
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["metrics"]["false_exclusion_rate"]["value"], 0.5)
        self.assertEqual(result["metrics"]["precision"]["value"], 1)
        self.assertEqual(result["sample_sizes"]["matched_labeled"], 1)
        self.assertEqual(result["status"], "FAIL")

    def test_failed_location_verification_and_broken_counts_cannot_report_pass(self):
        cell = self.evidence["docs"][0]["evidence"][0]
        cell.update(value="UNKNOWN", issues=["引文不在指定页"])
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["metrics"]["evidence_location_completeness"]["value"], 0)
        self.assertEqual(result["metrics"]["evidence_location_completeness"]["assessment"], "未达标")
        self.evidence["counts"]["candidates"] = 2
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["metrics"]["conservation_difference"]["value"], 1)
        self.assertEqual(result["metrics"]["conservation_difference"]["assessment"], "未达标")
        self.evidence["counts"].update(candidates=1, matched=0, excluded=1)
        result = evaluate(self.evidence, self.labels)
        self.assertEqual(result["metrics"]["conservation_difference"]["value"], 0)
        self.assertEqual(result["metrics"]["conservation_difference"]["assessment"], "未达标")


if __name__ == "__main__":
    unittest.main()
