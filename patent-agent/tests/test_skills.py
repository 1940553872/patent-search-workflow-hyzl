"""Skill package and executable contract regression tests.

These cases use fictional but realistic patent-document shapes. They exercise
the actual schema/rule/evidence modules, not an LLM or a patent database. Passing
them is neither target-model validation nor real-data acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from shared.contracts import ContractError, validate
from search.modules.match import assess


class SkillPackageTests(unittest.TestCase):
    """Formal packaging checks; these intentionally do not grade prose quality."""

    def test_metadata_and_all_direct_resource_targets(self):
        stages = {"spec": "spec", "plan": "query_plan", "match": "match",
                  "gap": "gap", "review": "review"}
        for suffix, schema in stages.items():
            with self.subTest(skill=suffix):
                directory = APP / "search" / "skills" / ("cnps-" + suffix)
                text = (directory / "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"))
                frontmatter, body = text[4:].split("\n---\n", 1)
                # The packages use the simple, portable scalar YAML form.
                values = dict(re.findall(r"^([a-z-]+): (.+)$", frontmatter, re.M))
                self.assertEqual(values["name"], directory.name)
                self.assertRegex(values["name"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                self.assertLessEqual(len(values["name"]), 64)
                self.assertTrue(1 <= len(values["description"]) <= 1024)
                metadata = dict(re.findall(r'^  ([a-z-]+): "([^"\n]+)"$', frontmatter, re.M))
                self.assertRegex(metadata["version"], r"^\d+\.\d+\.\d+$")
                self.assertEqual(metadata["cnps-stage"], suffix.upper())
                self.assertLessEqual(len(body.splitlines()), 500)
                links = re.findall(r"\]\(([^)]+)\)", body)
                self.assertIn(f"../../../shared/schemas/{schema}.schema.json", links)
                for target in links:
                    self.assertNotIn("\\", target)
                    resolved = (directory / target).resolve()
                    self.assertTrue(resolved.is_relative_to(APP), target)
                    self.assertTrue(resolved.is_file(), target)
                    if target.startswith("references/"):
                        # Supporting rules must not hide a further reading chain.
                        self.assertNotRegex(resolved.read_text(encoding="utf-8"), r"\]\([^)]*\)")


def sample_spec():
    return {"entry": "search", "intent": "新颖性", "base_date": "2024-06-01",
            "read_scope": "full", "keys": [],
            "features": [{"fid": "F1", "text": "检测气体异常"},
                         {"fid": "F2", "text": "检测电压突降"},
                         {"fid": "F3", "text": "两者同时异常才报警"}],
            "hard": {"op": "AND", "args": [{"feature": "F1"}, {"feature": "F2"}, {"feature": "F3"}]},
            "soft": ["储能场景优先"], "needs_human": []}


def sample_record():
    return {"key": "CN123456789A", "authority": "CN", "kind": "A",
            "dates": {"app": "2021-06-01", "pub": "2022-01-01"}}


def sample_materials():
    quote = "实施例1检测气体异常及电压突降，两者同时异常才报警。"
    document = {"key": "CN123456789A", "sha256": "a" * 64,
                "quality": {"identity_verified": True, "text_available": True,
                            "complete": True, "needs_ocr": False},
                "pages": [{"page": 3, "text": "[0012] " + quote}]}
    item = {"key": document["key"], "document_sha256": document["sha256"],
            "model": "fixture-agent", "context_id": "fixture-match-context",
            "features": [{"fid": fid, "state": "有支持", "quote": quote,
                          "page": 3, "loc": "[0012]", "scheme": "实施例1", "read": "全文"}
                         for fid in ("F1", "F2", "F3")]}
    return document, item


class SkillOutputRegressionTests(unittest.TestCase):
    def test_spec_preserves_soft_preference_and_rejects_foreign_fields(self):
        spec = sample_spec()
        validate("spec", spec)
        bad = deepcopy(spec)
        bad["lookup_numbers"] = []
        with self.assertRaises(ContractError):
            validate("spec", bad)
        document, item = sample_materials()
        # No storage keyword in the document: soft preference still cannot exclude.
        self.assertEqual(assess(sample_record(), spec, document, item)["class"], "匹配")

    def test_missing_voltage_is_unknown_not_a_semantic_exclusion(self):
        document, item = sample_materials()
        for feature in item["features"][1:]:
            feature.update(state="未找到", quote="", page=None, loc="", scheme="")
        validate("match", {"items": [item]})
        result = assess(sample_record(), sample_spec(), document, item)
        self.assertEqual(result["class"], "待核实")
        self.assertEqual([entry["value"] for entry in result["evidence"]], ["PASS", "UNKNOWN", "UNKNOWN"])

    def test_two_embodiments_cannot_be_combined_for_and_features(self):
        document, item = sample_materials()
        item["features"][1]["scheme"] = "实施例2"
        validate("match", {"items": [item]})
        result = assess(sample_record(), sample_spec(), document, item)
        self.assertEqual(result["class"], "待核实")
        self.assertEqual(result["value"], "UNKNOWN")

    def test_abstract_and_wrong_publication_version_cannot_support_match(self):
        for change in ("abstract", "wrong_version", "missing_pages"):
            with self.subTest(change=change):
                document, item = sample_materials()
                if change == "abstract":
                    for feature in item["features"]:
                        feature["read"] = "摘要"
                elif change == "wrong_version":
                    document["key"] = "CN123456789B"
                else:
                    document["quality"]["complete"] = False
                validate("match", {"items": [item]})
                result = assess(sample_record(), sample_spec(), document, item)
                self.assertEqual(result["class"], "待核实")

    def test_publication_on_base_date_remains_conflicting_application_candidate(self):
        document, item = sample_materials()
        record = sample_record()
        record["dates"]["pub"] = "2024-06-01"
        result = assess(record, sample_spec(), document, item)
        self.assertEqual(result["branch"], "抵触申请")
        self.assertEqual(result["class"], "待核实")

    def test_read_scope_and_actual_read_are_distinct_contracts(self):
        _, item = sample_materials()
        item["features"][0]["read"] = "full"
        with self.assertRaises(ContractError):
            validate("match", {"items": [item]})

    def test_plan_gap_and_review_realistic_outputs_fit_the_shared_contracts(self):
        query = {"qid": "Q2", "query": 'TACD_ALL:("电压突降" AND "气体检测") AND AUTHORITY:(CN)',
                 "purpose": "电压突降与气体检测联合报警候选。", "search_mode": "expert",
                 "fids": ["F2", "F3"], "required": True, "source": "patsnap_web"}
        validate("query_plan", {"queries": [query]})
        validate("gap", {"action": "evidence", "reason": "该候选只有摘要，需要对应 A 公开文本的完整 PDF。",
                         "queries": [], "keys": ["CN123456789A"]})
        validate("review", {"model": "fixture-review-agent", "context_id": "fixture-independent-review",
                            "items": [{"key": "CN123456789A", "verdict": "revise",
                                       "reason": "F2 与 F3 引文分属不同实施例，需改为未知或补充同一方案证据。"}]})
        # A Skill must not pass a final classification as its review opinion.
        with self.assertRaises(ContractError):
            validate("review", {"model": "fixture-review-agent", "context_id": "fixture-independent-review",
                                "items": [{"key": "CN123456789A", "verdict": "匹配", "reason": "通过"}]})


if __name__ == "__main__":
    unittest.main()
