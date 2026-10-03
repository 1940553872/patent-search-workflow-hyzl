"""Formal acceptance of date boundaries, three-value logic and evidence traps.

These are deterministic fixed fixtures, not claims of real-world search recall
or semantic accuracy. No live patent service or model call is involved.
"""

import copy
import json
import unittest
from pathlib import Path

from shared.rules import FAIL, PASS, UNKNOWN, FIELDS, evaluate
from search.modules.filter import assess as filter_assess
from search.modules.match import EVIDENCE_VALUES, assess as match_assess


def group(op, *args):
    return {"op": op, "args": list(args)}


def feature(fid):
    return {"feature": fid}


def field(name, cmp, value):
    return {"field": name, "cmp": cmp, "value": value}


class ThreeValueAcceptance(unittest.TestCase):
    def test_complete_truth_tables(self):
        expected_and = [[PASS, FAIL, UNKNOWN], [FAIL, FAIL, FAIL], [UNKNOWN, FAIL, UNKNOWN]]
        expected_or = [[PASS, PASS, PASS], [PASS, FAIL, UNKNOWN], [PASS, UNKNOWN, UNKNOWN]]
        states = [PASS, FAIL, UNKNOWN]
        for i, a in enumerate(states):
            for j, b in enumerate(states):
                with self.subTest(a=a, b=b):
                    values = {"F1": a, "F2": b}
                    self.assertEqual(evaluate(group("AND", feature("F1"), feature("F2")), {}, values), expected_and[i][j])
                    self.assertEqual(evaluate(group("OR", feature("F1"), feature("F2")), {}, values), expected_or[i][j])
        for value, expected in [(PASS, FAIL), (FAIL, PASS), (UNKNOWN, UNKNOWN)]:
            self.assertEqual(evaluate(group("NOT", feature("F1")), {}, {"F1": value}), expected)

    def test_empty_and_and_invalid_nodes(self):
        self.assertEqual(evaluate(group("AND"), {}), PASS)
        self.assertEqual(evaluate(group("OR"), {}), FAIL)
        for node in [None, {}, group("X"), group("NOT"), {"op": "AND", "args": "bad"}]:
            self.assertEqual(evaluate(node, {}), UNKNOWN)

    def test_missing_and_conflicted_fields_are_unknown(self):
        node = field("dates.pub", "lt", "2024-06-01")
        for record in [{}, {"dates": {"pub": "2023-01"}},
                       {"dates": {"pub": "2023-01-01"}, "conflicts": {"dates.pub": ["2023-01-01", "2025-01-01"]}},
                       {"dates": {"pub": "2023-01-01"}, "conflicts": {"dates": {"pub": [1, 2]}}}]:
            self.assertEqual(evaluate(node, record), UNKNOWN)

    def test_unrelated_conflict_does_not_erase_known_field(self):
        record = {"dates": {"pub": "2023-01-01"}, "conflicts": {"dates.app": [1, 2]}}
        self.assertEqual(evaluate(field("dates.pub", "lt", "2024-06-01"), record), PASS)

    def test_list_and_date_comparisons(self):
        record = {"applicants": ["甲公司", "乙研究院"], "authority": "CN", "dates": {"pub": "2024-06-01"}}
        self.assertEqual(evaluate(field("applicants", "eq", "甲公司"), record), PASS)
        self.assertEqual(evaluate(field("applicants", "contains", "研究院"), record), PASS)
        self.assertEqual(evaluate(field("authority", "in", ["CN", "WO"]), record), PASS)
        self.assertEqual(evaluate(field("dates.pub", "lt", "2024-06-01"), record), FAIL)
        self.assertEqual(evaluate(field("dates.pub", "gte", "2024-06-01"), record), PASS)

    def test_supported_fields_match_published_spec_contract(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "shared/schemas/spec.schema.json").read_text(encoding="utf-8"))
        field_node = next(node for node in schema["$defs"]["tree"]["anyOf"] if "field" in node["properties"])
        self.assertEqual(FIELDS, set(field_node["properties"]["field"]["enum"]))

    def test_added_fields_and_all_dates_are_compared_by_type(self):
        record = {"dates": {"pri": "2024-01-01"}, "legal": {"expiry": "2040-01-01", "as_of": "2026-10-03"},
                  "ipc": ["G06F16/31"], "title": "一种专利检索方法", "family_id": "FAM-1", "application_number": "CN202410000001.1"}
        cases = [("dates.pri", "lt", "2024-06-01"), ("legal.expiry", "gt", "2026-10-03"),
                 ("legal.as_of", "eq", "2026-10-03"), ("ipc", "contains", "G06F16"),
                 ("title", "contains", "专利检索"), ("family_id", "eq", "FAM-1"),
                 ("application_number", "eq", "CN202410000001.1")]
        for name, cmp, expected in cases:
            self.assertEqual(evaluate(field(name, cmp, expected), record), PASS)
        for name in ("dates.pri", "legal.expiry", "legal.as_of"):
            self.assertEqual(evaluate(field(name, "lt", "2024-99-99"), record), UNKNOWN)
            self.assertEqual(evaluate(field(name, "in", ["2024-01-01", "invalid"]), record), UNKNOWN)


class ScreeningAcceptance(unittest.TestCase):
    def setUp(self):
        self.spec = {"entry": "search", "intent": "新颖性", "base_date": "2024-06-01", "hard": group("AND"), "soft": []}
        self.record = {"authority": "CN", "dates": {"app": "2023-01-01", "pub": "2024-05-31"}, "conflicts": {}}

    def test_date_boundaries(self):
        cases = [("2024-05-31", "2023-01-01", PASS, "现有技术"),
                 ("2024-06-01", "2024-05-31", PASS, "抵触申请"),
                 ("2024-06-01", "2024-06-01", FAIL, None),
                 ("2025-01-01", "2025-01-01", FAIL, None)]
        for pub, app, value, branch in cases:
            with self.subTest(pub=pub, app=app):
                self.record["dates"] = {"pub": pub, "app": app}
                result = filter_assess(self.record, self.spec)
                self.assertEqual((result["value"], result["branch"]), (value, branch))

    def test_priority_date_never_substitutes_for_application_or_publication(self):
        self.record["dates"] = {"pri": "2020-01-01"}
        self.assertEqual(filter_assess(self.record, self.spec)["value"], UNKNOWN)
        self.record["dates"]["pub"] = "2024-07-01"
        self.assertEqual(filter_assess(self.record, self.spec)["value"], UNKNOWN)

    def test_missing_base_date_is_unknown_and_never_inferred_from_today(self):
        for base in (None, ""):
            with self.subTest(base=base):
                self.spec["base_date"] = base
                result = filter_assess(self.record, self.spec, "2026-10-03")
                self.assertEqual(result["value"], UNKNOWN)
                self.assertIsNone(result["branch"])
                self.assertEqual(match_assess(self.record, self.spec)["class"], "待核实")

    def test_later_foreign_publication_is_not_conflicting_chinese_application(self):
        self.record.update(authority="WO", dates={"app": "2023-01-01", "pub": "2024-06-01"})
        self.assertEqual(filter_assess(self.record, self.spec)["value"], FAIL)

    def test_panorama_adds_no_cutoff_and_lookup_does_not_filter(self):
        self.spec["intent"] = "全景"
        self.record["dates"]["pub"] = "2028-01-01"
        self.assertEqual(filter_assess(self.record, self.spec)["value"], PASS)
        self.spec.update(entry="lookup", hard=field("authority", "eq", "US"))
        self.assertEqual(filter_assess(self.record, self.spec)["value"], PASS)

    def test_technical_unknown_does_not_override_field_or(self):
        self.spec["hard"] = group("OR", feature("F1"), field("authority", "eq", "CN"))
        self.assertEqual(filter_assess(self.record, self.spec)["value"], PASS)

    def test_soft_preference_cannot_exclude(self):
        self.spec["soft"] = [field("authority", "eq", "US")]
        self.assertEqual(filter_assess(self.record, self.spec)["value"], PASS)

    def test_risk_requires_current_unconflicted_cn_status(self):
        self.spec["intent"] = "侵权风险"
        self.record["legal"] = {"state": "有效", "as_of": "2026-10-03"}
        self.assertEqual(filter_assess(self.record, self.spec, "2026-10-03")["value"], PASS)
        self.record["legal"]["as_of"] = "2026-10-02"
        self.assertEqual(filter_assess(self.record, self.spec, "2026-10-03")["value"], UNKNOWN)
        self.record["legal"]["as_of"] = "2026-10-03"
        self.record["conflicts"] = {"legal.state": ["有效", "失效"]}
        self.assertEqual(filter_assess(self.record, self.spec, "2026-10-03")["value"], UNKNOWN)
        self.record["authority"] = "US"
        self.assertEqual(filter_assess(self.record, self.spec, "2026-10-03")["value"], FAIL)

    def test_risk_current_expired_state_excludes_but_contradictory_expiry_is_unknown(self):
        self.spec["intent"] = "侵权风险"
        self.record["legal"] = {"state": "失效", "as_of": "2026-10-03"}
        self.assertEqual(filter_assess(self.record, self.spec, "2026-10-03")["value"], FAIL)
        self.record["legal"].update(state="有效", expiry="2025-10-03")
        self.assertEqual(filter_assess(self.record, self.spec, "2026-10-03")["value"], UNKNOWN)


class EvidenceAcceptance(unittest.TestCase):
    def setUp(self):
        self.record = {"key": "CN123456789A", "authority": "CN", "dates": {"app": "2021-01-01", "pub": "2022-01-01"}}
        self.spec = {"entry": "search", "intent": "新颖性", "base_date": "2024-06-01", "read_scope": "full",
                     "features": [{"fid": "F1", "text": "气体"}, {"fid": "F2", "text": "电压"}],
                     "hard": group("AND", feature("F1"), feature("F2")), "soft": []}
        self.document = {"key": self.record["key"], "sha256": "a" * 64, "path": "fixture.pdf",
                         "pages": [{"page": 1, "text": "[0001]检测气体与电压。\n[0002]仅检测温度。"},
                                   {"page": 2, "text": "1. 一种检测气体与电压的装置。\n2. 如权利要求1所述的装置。"}],
                         "quality": {"text_available": True, "needs_ocr": False, "identity_verified": True, "complete": True, "issues": []}}
        self.submission = {"key": self.record["key"], "document_sha256": self.document["sha256"],
                           "model": "formal-fixture", "context_id": "match-context", "features": [
                               {"fid": fid, "state": "有支持", "quote": "检测气体与电压。", "page": 1,
                                "loc": "说明书[0001]", "scheme": "实施例1", "read": "全文"} for fid in ("F1", "F2")]}

    def result(self):
        return match_assess(self.record, self.spec, self.document, self.submission)

    def test_same_publication_same_scheme_exact_quotes_match(self):
        self.assertEqual(self.result()["class"], "匹配")
        self.assertTrue(all(item["value"] == PASS for item in self.result()["evidence"]))

    def test_conflicting_application_always_pending(self):
        self.record["dates"]["pub"] = "2024-06-01"
        self.assertEqual((self.result()["class"], self.result()["branch"]), ("待核实", "抵触申请"))

    def test_fields_can_exclude_without_document(self):
        self.record["dates"] = {"app": "2025-01-01", "pub": "2025-01-01"}
        self.assertEqual(match_assess(self.record, self.spec)["class"], "排除")

    def test_missing_and_abstract_only_evidence_are_pending(self):
        self.assertEqual(match_assess(self.record, self.spec)["class"], "待核实")
        for item in self.submission["features"]:
            item["read"] = "摘要"
        self.assertEqual(self.result()["class"], "待核实")

    def test_wrong_publication_version_or_hash_never_matches(self):
        for key, value in [("key", "CN123456789B"), ("document_sha256", "b" * 64)]:
            with self.subTest(key=key):
                submission = copy.deepcopy(self.submission)
                submission[key] = value
                self.assertEqual(match_assess(self.record, self.spec, self.document, submission)["class"], "待核实")

    def test_missing_pages_and_unverified_identity_never_match(self):
        for key in ["complete", "identity_verified", "text_available"]:
            with self.subTest(key=key):
                document = copy.deepcopy(self.document)
                document["quality"][key] = False
                self.assertEqual(match_assess(self.record, self.spec, document, self.submission)["class"], "待核实")

    def test_quote_must_be_verbatim_in_stated_page_and_location(self):
        for key, value in [("quote", "改写的气体和电压"), ("page", 2), ("loc", "第1页"), ("loc", "[0002]")]:
            with self.subTest(key=key, value=value):
                submission = copy.deepcopy(self.submission)
                submission["features"][0][key] = value
                self.assertEqual(match_assess(self.record, self.spec, self.document, submission)["class"], "待核实")

    def test_cross_example_combination_cannot_match(self):
        self.submission["features"][1]["scheme"] = "实施例2"
        self.assertEqual(self.result()["class"], "待核实")

    def test_not_does_not_turn_cross_example_unknown_into_true(self):
        self.spec["hard"] = group("NOT", self.spec["hard"])
        self.submission["features"][1]["scheme"] = "实施例2"
        self.assertEqual(self.result()["class"], "待核实")

    def test_not_cannot_bypass_full_document_requirement(self):
        self.spec["hard"] = group("NOT", feature("F1"))
        self.spec["read_scope"] = "claims"
        self.submission["features"][0]["state"] = "明确相反"
        self.document["quality"]["complete"] = False
        self.assertEqual(self.result()["class"], "待核实")

    def test_six_states_are_strict(self):
        expected = {"有支持": "匹配", "明确相反": "排除", "部分支持": "待核实", "未找到": "待核实", "材料不可得": "待核实", "来源冲突": "待核实"}
        self.assertEqual(set(EVIDENCE_VALUES), set(expected))
        for state, classification in expected.items():
            with self.subTest(state=state):
                self.submission["features"][0]["state"] = state
                self.assertEqual(self.result()["class"], classification)

    def test_missing_feature_defaults_to_material_unavailable(self):
        self.submission["features"].pop()
        result = self.result()
        self.assertEqual(result["class"], "待核实")
        self.assertEqual(result["evidence"][1]["state"], "材料不可得")

    def test_same_scheme_conflicting_states_are_unknown(self):
        opposite = dict(self.submission["features"][0], state="明确相反")
        self.submission["features"].append(opposite)
        self.assertEqual(self.result()["class"], "待核实")

    def test_or_can_be_satisfied_by_one_scheme(self):
        self.spec["hard"] = group("OR", feature("F1"), feature("F2"))
        self.submission["features"][1]["scheme"] = "实施例2"
        self.assertEqual(self.result()["class"], "匹配")

    def test_field_or_can_pass_without_semantic_evidence(self):
        self.spec["hard"] = group("OR", feature("F1"), field("authority", "eq", "CN"))
        self.assertEqual(match_assess(self.record, self.spec)["class"], "匹配")

    def test_claim_location_is_checked_and_actual_read_is_retained(self):
        self.spec["read_scope"] = "claims"
        for item in self.submission["features"]:
            item.update(quote="一种检测气体与电压的装置。", page=2, loc="权利要求1", read="权利要求")
        self.assertEqual(self.result()["class"], "匹配")
        self.assertEqual(self.result()["evidence"][0]["read"], "权利要求")

    def test_claims_complete_allows_scoped_match_and_not_without_full_text(self):
        self.spec["read_scope"] = "claims"
        self.document["quality"].update(complete=False, claims_complete=True)
        for item in self.submission["features"]:
            item.update(quote="一种检测气体与电压的装置。", page=2, loc="权利要求1", read="权利要求")
        self.assertEqual(self.result()["class"], "匹配")
        self.spec["hard"] = group("NOT", feature("F1"))
        self.submission["features"][0]["state"] = "明确相反"
        self.assertEqual(self.result()["class"], "匹配")
        self.document["quality"]["claims_complete"] = False
        self.assertEqual(self.result()["class"], "待核实")

    def test_claims_scope_rejects_description_citation_and_unconfirmed_read_depth(self):
        self.spec["read_scope"] = "claims"
        self.document["quality"].update(complete=False, claims_complete=True)
        for item in self.submission["features"]:
            item["read"] = "权利要求"
        self.assertEqual(self.result()["class"], "待核实")
        for item in self.submission["features"]:
            item.update(quote="一种检测气体与电压的装置。", page=2, loc="权利要求1", read="全文")
        self.assertEqual(self.result()["class"], "待核实")

    def test_verbatim_quote_may_include_paragraph_marker(self):
        self.submission["features"][0]["quote"] = "[0001]检测气体与电压。"
        self.assertEqual(self.result()["class"], "匹配")

    def test_panorama_lookup_and_risk_do_not_run_semantic_match(self):
        self.spec["intent"] = "全景"
        self.assertIsNone(self.result()["class"])
        self.spec["intent"] = "侵权风险"
        self.assertEqual(self.result()["class"], "待核实")
        self.spec["entry"] = "lookup"
        self.assertIsNone(self.result()["class"])

    def test_design_five_document_fixed_replay_conserves_classes(self):
        results = [self.result()["class"]]
        later = dict(self.record, dates={"app": "2025-01-01", "pub": "2025-01-01"})
        results.append(match_assess(later, self.spec)["class"])
        conflict = dict(self.record, dates={"app": "2023-11-01", "pub": "2024-09-01"})
        results.append(match_assess(conflict, self.spec, self.document, self.submission)["class"])
        partial = copy.deepcopy(self.submission)
        partial["features"][1]["state"] = "未找到"
        results.append(match_assess(self.record, self.spec, self.document, partial)["class"])
        results.append(match_assess(self.record, self.spec)["class"])
        self.assertEqual({name: results.count(name) for name in set(results)}, {"匹配": 1, "排除": 1, "待核实": 3})


if __name__ == "__main__":
    unittest.main()
