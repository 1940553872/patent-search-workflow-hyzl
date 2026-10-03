"""Formal regressions for deterministic retrieval handoffs and migration.

No live patent database is queried; hit counts here are synthetic user records.
"""
from pathlib import Path
import csv
import gc
import shutil
import sys
import unittest
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from shared.contracts import ContractError, validate
from search.modules.queries import validate_expression
from workflow import Workflow

QUERY = 'TACD_ALL:("分层任务网络" OR "hierarchical task network") AND AUTHORITY:(CN)'
KEYS = ["CN123456781A", "CN123456782A", "CN123456783A", "CN123456784A"]


class OrdinaryQueryAcceptance(unittest.TestCase):
    def setUp(self):
        self.root = APP / "tests" / (".query-" + uuid4().hex)
        self.root.mkdir()
        self.w = Workflow(self.root / "data")

    def tearDown(self):
        self.w = None
        gc.collect()
        if self.root.resolve().parent != (APP / "tests").resolve() or not self.root.name.startswith(".query-"):
            raise RuntimeError("验收目录范围异常，停止清理")
        shutil.rmtree(self.root)

    def lookup(self):
        return self.w.create({"entry": "lookup", "keys": KEYS, "read_scope": "biblio", "caller": "cnip",
                              "approved_sources": ["patsnap_web"], "approved_models": ["codex"]})["run_id"]

    def search_plan(self):
        rid = self.w.create({"raw_input": "领域本体与任务规划（验收合成需求）", "caller": "cnnv",
                             "approved_sources": ["patsnap_web"], "approved_models": ["codex"]})["run_id"]
        self.submit(rid, {"entry": "search", "intent": "新颖性", "base_date": "2024-06-01",
                          "read_scope": "full", "keys": [], "features": [{"fid": "F1", "text": "任务规划"}],
                          "hard": {"feature": "F1"}, "soft": [], "needs_human": []})
        self.submit(rid, {"queries": [{"qid": "Q1", "query": QUERY, "purpose": "任务规划候选",
                                      "search_mode": "expert", "fids": ["F1"], "required": True, "source": "patsnap_web"}]})
        return rid

    def submit(self, rid, output, packet=None):
        packet = packet or self.w.packet(rid)
        return self.w.submit(rid, packet["stage"], {"task_token": packet["task_token"], "model": "codex",
                           "context_id": "formal-query-context", "elapsed_seconds": 1, "output": output})

    def csv(self, name, keys):
        path = self.root / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["公开号", "标题", "申请日", "公开日"])
            writer.writerows([[key, "synthetic planning", "2020-01-01", "2021-01-01"] for key in keys])
        return path

    def test_translated_text_phrases_and_lookup_have_executable_fields(self):
        self.assertEqual(validate_expression(QUERY), ["AUTHORITY", "TACD_ALL"])
        rid = self.lookup()
        query = self.w.view(rid)["queries"][0]
        self.assertEqual(query["search_mode"], "expert")
        self.assertEqual(validate_expression(query["query"]), ["PN"])
        self.assertNotIn("prompt", query)

    def test_malformed_queries_and_unknown_fields_are_rejected(self):
        invalid = ["请帮我查找分层任务网络", 'TACD_ALL:("HTN") AND', 'TACD_ALL:("HTN"',
                   'TACD_ALL:("HTN)', 'TACD_ALL:(hierarchical task network)',
                   'MADE_UP:("HTN")', 'TACD_ALL:() AND AUTHORITY:(CN)',
                   'AUTHORITY:(CN)', 'TACD_ALL:("HTN")\n请查找专利']
        for expression in invalid:
            with self.subTest(expression=expression), self.assertRaises(ContractError):
                validate_expression(expression)

    def test_new_plan_rejects_agent_prompt_contract(self):
        query = {"qid": "Q1", "query": QUERY, "prompt": "请用芽仔检索", "fids": ["F1"],
                 "required": True, "source": "patsnap_web"}
        with self.assertRaises(ContractError):
            validate("query_plan", {"queries": [query]})
        with self.assertRaises(ContractError):
            validate("gap", {"action": "retrieve", "reason": "补检", "queries": [query], "keys": []})

    def test_partial_export_cannot_be_declared_complete_and_pages_can_finish(self):
        rid = self.lookup()
        query = self.w.view(rid)["queries"][0]["query"]
        execution = {"actual_query": query, "total_hits": 4, "searched_at": "2024-06-01"}
        first = self.csv("first.csv", KEYS[:2])
        run = self.w.import_file(rid, first, "Qlookup", execution=execution)
        self.assertEqual(run["queries"][0]["status"], "truncated")
        self.assertEqual(run["queries"][0]["imported_unique"], 2)
        run = self.w.import_file(rid, self.csv("last.csv", KEYS[2:]), "Qlookup", page=2, execution=execution)
        self.assertEqual(run["queries"][0]["status"], "complete")
        self.assertEqual(run["stage"], "EXPORT")
        self.assertEqual(run["queries"][0]["imported_unique"], 4)

    def test_duplicate_export_does_not_satisfy_reported_total(self):
        rid = self.lookup()
        execution = {"actual_query": self.w.view(rid)["queries"][0]["query"], "total_hits": 4}
        path = self.csv("duplicate.csv", KEYS[:2])
        self.w.import_file(rid, path, "Qlookup", execution=execution)
        run = self.w.import_file(rid, path, "Qlookup", page=2, execution=execution)
        self.assertEqual(run["queries"][0]["status"], "truncated")
        self.assertEqual(run["queries"][0]["imported_unique"], 2)

    def test_changed_actual_query_is_rejected_without_changing_saved_data(self):
        rid = self.lookup()
        path = self.csv("partial.csv", KEYS[:1])
        self.w.import_file(rid, path, "Qlookup", status="truncated", execution={"actual_query": "PN:(" + KEYS[0] + ")"})
        before = self.w.store.load(rid)
        with self.assertRaises(ContractError):
            self.w.import_file(rid, path, "Qlookup", status="truncated", execution={"actual_query": "PN:(" + KEYS[1] + ")"})
        self.assertEqual(self.w.store.load(rid), before)

    def test_replan_preserves_materials_spec_budget_and_query_history(self):
        rid = self.search_plan()
        self.w.import_file(rid, self.csv("partial.csv", KEYS[:1]), "Q1", status="truncated")
        before = self.w.store.load(rid)
        run = self.w.replan(rid, "用户要求普通关键词检索", "合成验收指示")
        self.assertEqual(run["stage"], "PLAN")
        self.assertEqual(run["queries"][0]["status"], "superseded")
        self.assertFalse(run["queries"][0]["required"])
        for field in ("spec", "records", "batches", "config", "used_seconds", "round"):
            self.assertEqual(run[field], before[field])
        with self.assertRaises(ContractError):
            self.w.import_file(rid, query="Q1", status="zero")
        packet = self.w.packet(rid)
        query = {"qid": "Q2", "query": QUERY, "purpose": "新普通检索路径", "search_mode": "expert",
                 "fids": ["F1"], "required": True, "source": "patsnap_web"}
        run = self.submit(rid, {"queries": [query]}, packet)
        self.assertEqual([q["qid"] for q in run["queries"]], ["Q1", "Q2"])
        self.assertEqual(run["status"], "WAITING_IMPORT")

    def test_replan_keeps_completed_queries_and_rejects_old_task_token(self):
        rid = self.search_plan()
        self.w.import_file(rid, query="Q1", status="zero")
        run = self.w.replan(rid, "调整查询路径", "验收员")
        self.assertEqual(run["queries"][0]["status"], "zero")
        self.assertTrue(run["queries"][0]["required"])
        packet = self.w.packet(rid)
        self.w.replan(rid, "再次明确普通检索", "验收员")
        with self.assertRaises(ContractError):
            self.submit(rid, {"queries": [{"qid": "Q2", "query": QUERY, "purpose": "新路径", "search_mode": "expert",
                                          "fids": ["F1"], "required": True, "source": "patsnap_web"}]}, packet)

    def test_old_generated_guide_is_replaced_and_user_edits_are_protected(self):
        rid = self.search_plan()
        run = self.w.store.load(rid)
        self.w.store.artifact(run, "search_prompts.md", "legacy generated guide")
        self.w.store.save(run, "FIXTURE_GUIDE")
        guide = self.w.queries(rid)
        self.assertFalse((self.w.store.run_dir(rid) / "search_prompts.md").exists())
        self.assertEqual(Path(guide["path"]).name, "search_queries.md")
        self.assertIn(QUERY, guide["text"])
        run = self.w.store.load(rid)
        old = self.w.store.artifact(run, "search_prompts.md", "generated second guide")
        self.w.store.save(run, "FIXTURE_GUIDE")
        old.write_text("user latest edit", encoding="utf-8")
        before = Path(guide["path"]).read_bytes()
        with self.assertRaises(ContractError):
            self.w.queries(rid)
        self.assertEqual(old.read_text(encoding="utf-8"), "user latest edit")
        self.assertEqual(Path(guide["path"]).read_bytes(), before)

    def test_unknown_execution_values_are_rejected(self):
        rid = self.lookup()
        for execution in ({"total_hits": -1}, {"total_hits": True}, {"searched_at": "2024-99-01"},
                          {"searched_at": None}, {"actual_query": ""}):
            with self.subTest(execution=execution), self.assertRaises(ContractError):
                self.w.import_file(rid, query="Qlookup", status="zero", execution=execution)


if __name__ == "__main__":
    unittest.main()
