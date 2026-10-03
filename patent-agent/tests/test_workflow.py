"""Formal local workflow acceptance with synthetic patents and real PDF parsing.

These fixtures exercise contracts and state transitions.  They do not establish
real-world retrieval recall or the semantic accuracy of a live Agent.
"""
from __future__ import annotations

from copy import deepcopy
import csv
import gc
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from shared.contracts import ContractError
from shared.storage import now
from workflow import Workflow
from service import SearchService
from test_ingestion import make_pdf


KEYS = [f"CN12345000{number}A" for number in range(1, 6)]
QUOTES = {
    "F1": "A gas sensor detects electrolyte leakage gas.",
    "F2": "The controller monitors a sudden cell voltage drop.",
    "F3": "The alarm starts only when both conditions are satisfied.",
}


class WorkflowAcceptance(unittest.TestCase):
    def test_public_search_contract_accepts_feature_only_request_and_stays_partial(self):
        service = SearchService(self.root / "service-data")
        result = service.search({"request": {"intent": "新颖性", "features": [{"fid": "F1", "text": "gas detection"}],
                                  "base_date": "2024-06-01", "caller": "cnnv"}})
        self.assertEqual(result["status"], "PARTIAL")
        self.assertFalse(result["coverage_complete"])
        self.assertFalse(result["review_complete"])
        self.assertEqual(result["scope"]["workflow_state"]["stage"], "SPEC")
        self.assertEqual(service.evidence(result["query_id"])["query_id"], result["query_id"])

    def test_public_lookup_contract_returns_pending_import_and_specific_keys(self):
        service = SearchService(self.root / "service-data")
        result = service.lookup({"keys": [KEYS[0]], "read_scope": "claims", "caller": "cnip"})
        self.assertEqual(result["status"], "PENDING_IMPORT")
        self.assertEqual(result["request"]["keys"], [KEYS[0]])
        self.assertIsNone(result["request"]["intent"])
        self.assertTrue(result["import_todo"])

    def setUp(self):
        self.root = Path(__file__).resolve().parent / (".workflow-" + uuid4().hex)
        self.root.mkdir()
        self.w = Workflow(self.root / "data")

    def tearDown(self):
        # Ensure short-lived SQLite connections have been finalized on Windows.
        self.w = None
        gc.collect()
        parent = Path(__file__).resolve().parent
        if self.root.resolve().parent != parent or not self.root.name.startswith(".workflow-"):
            raise RuntimeError("验收目录范围异常，停止清理。")
        shutil.rmtree(self.root)

    def submit(self, rid, output, *, context=None, stage=None):
        packet = self.w.packet(rid)
        stage = stage or packet["stage"]
        context = context or stage.lower() + "-" + uuid4().hex
        envelope = {"task_token": packet["task_token"], "model": "codex", "context_id": context,
                    "elapsed_seconds": 1, "output": output}
        return self.w.submit(rid, stage, envelope)

    def start(self, *, intent="新颖性", caller="cnnv", read_scope="full", hard_features=True):
        run = self.w.create({"raw_input": "电池气体与电压联合预警（合成验收资料）", "intent": intent,
                             "caller": caller, "read_scope": read_scope,
                             "approved_sources": ["patsnap_web"], "approved_models": ["codex"]})
        rid = run["run_id"]
        spec = {"entry": "search", "intent": intent, "base_date": "2024-06-01" if intent == "新颖性" else None,
                "read_scope": read_scope, "keys": [],
                "features": [{"fid": fid, "text": text} for fid, text in QUOTES.items()] if hard_features else [],
                "hard": {"op": "AND", "args": [{"feature": fid} for fid in QUOTES] if hard_features else []},
                "soft": [], "needs_human": []}
        run = self.submit(rid, spec)
        if caller == "standalone":
            self.assertEqual(run["status"], "WAITING_HUMAN")
            run = self.w.confirm(rid, "spec", "验收员")
        self.assertEqual(run["stage"], "PLAN")
        run = self.submit(rid, {"queries": [{"qid": "Q1", "query": "battery AND gas sensor",
                                            "prompt": "检索同时涉及电池气体检测与电压检测的专利，保留完整公开号。",
                                            "fids": list(QUOTES) if hard_features else [],
                                            "required": True, "source": "patsnap_web"}]})
        self.assertEqual(run["status"], "WAITING_IMPORT")
        return rid

    def export_csv(self, rows=None, name="patents.csv"):
        path = self.root / name
        rows = rows if rows is not None else [
            [KEYS[0], "2021-01-01", "2022-01-01", "D1 synthetic matching example", "有效", now()[:10]],
            [KEYS[1], "2025-01-01", "2025-02-01", "D2 synthetic later application", "有效", now()[:10]],
            [KEYS[2], "2023-11-01", "2024-09-01", "D3 synthetic conflicting application", "有效", now()[:10]],
            [KEYS[3], "2021-01-01", "2022-01-01", "D4 synthetic missing features", "有效", now()[:10]],
            [KEYS[4], "2020-01-01", "2021-01-01", "D5 synthetic abstract only", "有效", now()[:10]],
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["公开号", "申请日", "公开日", "标题", "法律状态", "法律状态日期"])
            writer.writerows(rows)
        return path

    def add_pdf(self, rid, key, *, features=None, pages=None, confirm=True, scope="full"):
        inbox = Path(self.w.view(rid)["paths"]["pdf_inbox"])
        texts = pages if pages is not None else [key + "\n" + "\n".join(
            f"[{int(fid[1:]):04d}] {QUOTES[fid]}" for fid in (features if features is not None else QUOTES))]
        make_pdf(inbox / (key + ".pdf"), texts)
        self.w.ingest_pdfs(rid)
        if confirm:
            self.w.confirm_pdf(rid, key, "验收员", len(texts), scope=scope)

    def fill_matches(self, rid):
        run = self.w.view(rid)
        items = []
        for key in self.w.packet(rid)["input"]["records"]:
            document = run["documents"].get(key)
            features = []
            for fid in QUOTES:
                state = "有支持"
                if key == KEYS[3] and fid != "F1":
                    state = "未找到"
                elif key == KEYS[4]:
                    state = "材料不可得"
                supported = state == "有支持"
                features.append({"fid": fid, "state": state, "quote": QUOTES[fid] if supported else "",
                                 "page": 1 if supported else None, "loc": f"说明书[{int(fid[1:]):04d}]" if supported else "",
                                 "scheme": "实施例1" if document else "", "read": "全文" if document else "摘要"})
            items.append({"key": key, "document_sha256": document["sha256"] if document else "", "features": features})
        return self.submit(rid, {"items": items}, context="match-context")

    def populated(self, *, caller="cnnv"):
        rid = self.start(caller=caller)
        path = self.export_csv()
        self.w.import_file(rid, path, query="Q1")
        for key in (KEYS[0], KEYS[2]):
            self.add_pdf(rid, key)
        self.add_pdf(rid, KEYS[3], features=["F1"])
        self.fill_matches(rid)
        return rid

    def finish(self, rid, *, independent=True):
        run = self.submit(rid, {"action": "stop", "reason": "合成验收查询已执行，缺失材料保留待核实。", "queries": [], "keys": []})
        if run["stage"] == "REVIEW":
            packet = self.w.packet(rid)
            context = "review-context" if independent else "match-context"
            review = {"model": "codex", "context_id": context,
                      "items": [{"key": key, "verdict": "confirm", "reason": "已独立核验原文证据或确定性日期条件。"}
                                for key in packet["input"]["review_keys"]]}
            run = self.submit(rid, review, context=context)
        if run["request"]["caller"] == "standalone":
            self.w.confirm(rid, "export", "验收员")
        outputs = self.w.export(rid)
        return json.loads(Path(outputs["evidence"]).read_text(encoding="utf-8"))

    def test_d1_d5_complete_public_flow_and_three_way_conservation(self):
        rid = self.populated(caller="standalone")
        evidence = self.finish(rid)
        docs = {doc["key"]: doc for doc in evidence["docs"]}
        expected = {KEYS[0]: "匹配", KEYS[1]: "排除", KEYS[2]: "待核实", KEYS[3]: "待核实", KEYS[4]: "待核实"}
        self.assertEqual({key: doc["class"] for key, doc in docs.items()}, expected)
        self.assertEqual(docs[KEYS[2]]["branch"], "抵触申请")
        self.assertEqual(len(docs), sum(sum(doc["class"] == label for doc in docs.values()) for label in ("匹配", "排除", "待核实")))
        self.assertTrue(evidence["coverage_complete"])
        self.assertTrue(evidence["review_complete"])
        self.assertEqual(len(evidence["funnel"]), 5)
        self.assertEqual(self.w.view(rid)["status"], "DONE")
        self.assertTrue((self.w.store.run_dir(rid) / "report.html").is_file())

    def test_import_idempotence_and_resume_from_sqlite(self):
        rid = self.start()
        path = self.export_csv()
        first = self.w.import_file(rid, path, query="Q1")
        second = self.w.import_file(rid, path, query="Q1")
        self.assertEqual(len(first["batches"]), 1)
        self.assertEqual(len(second["batches"]), 1)
        self.assertEqual(len(second["records"]), 5)
        self.assertEqual(first["_revision"], second["_revision"])
        self.w = Workflow(self.root / "data")
        resumed = self.w.view(rid)
        self.assertEqual(resumed["stage"], "MATCH")
        self.assertEqual(resumed["records"], second["records"])
        self.assertEqual(resumed["batches"], second["batches"])

    def test_zero_hits_and_failed_query_remain_distinct(self):
        zero_rid = self.start()
        zero = self.w.import_file(zero_rid, query="Q1", status="zero")
        self.assertEqual(zero["queries"][0]["status"], "zero")
        self.assertEqual(zero["stage"], "GAP")
        empty = self.finish(zero_rid)
        self.assertTrue(empty["coverage_complete"])
        self.assertEqual(empty["docs"], [])
        failed_rid = self.start()
        failed = self.w.import_file(failed_rid, query="Q1", status="failed", note="官网导出服务暂时不可用")
        self.assertEqual(failed["queries"][0]["status"], "failed")
        self.assertEqual(failed["status"], "WAITING_IMPORT")
        self.w.stop(failed_rid, "失败原因已登记，等待人工重试。")
        evidence = json.loads(Path(self.w.export(failed_rid)["evidence"]).read_text(encoding="utf-8"))
        self.assertFalse(evidence["coverage_complete"])
        self.assertNotEqual(evidence["status"], "COMPLETE")

    def test_candidates_cap_records_truncation_and_preserves_partial_delivery(self):
        rid = self.start()
        run = self.w.store.load(rid)
        run["config"]["max_candidates"] = 2
        self.w.store.save(run, "ACCEPTANCE_CANDIDATE_LIMIT", {"max_candidates": 2})
        run = self.w.import_file(rid, self.export_csv(), query="Q1")
        self.assertEqual(len(run["records"]), 2)
        self.assertEqual(run["batches"][0]["omitted_rows"], 3)
        self.assertEqual(run["batches"][0]["status"], "truncated")
        self.assertEqual(run["status"], "WAITING_IMPORT")
        self.w.stop(rid, "候选上限已达到，交付已纳入部分。")
        evidence = json.loads(Path(self.w.export(rid)["evidence"]).read_text(encoding="utf-8"))
        self.assertEqual(len(evidence["docs"]), 2)
        self.assertFalse(evidence["coverage_complete"])
        self.assertNotEqual(evidence["status"], "COMPLETE")

    def test_budget_exhaustion_exports_partial_without_claiming_review(self):
        rid = self.start()
        self.w.import_file(rid, self.export_csv(), query="Q1")
        run = self.w.store.load(rid)
        run["used_seconds"] = run["request"]["budget_min"] * 60
        self.w.store.save(run, "ACCEPTANCE_BUDGET_BOUNDARY", {})
        stopped = self.w.advance(rid)
        self.assertTrue(stopped["budget_exhausted"])
        self.assertEqual(stopped["stage"], "EXPORT")
        evidence = json.loads(Path(self.w.export(rid)["evidence"]).read_text(encoding="utf-8"))
        self.assertNotEqual(evidence["status"], "COMPLETE")
        self.assertFalse(evidence["review_complete"])
        self.assertIn("预算", evidence["termination_reason"])

    def test_date_only_change_reuses_existing_evidence_and_raw_batches(self):
        rid = self.populated()
        before = self.w.view(rid)
        modified = deepcopy(before["spec"])
        modified["base_date"] = "2026-06-01"
        after = self.w.update_spec(rid, modified, "验收员")
        self.assertEqual(after["matches"], before["matches"])
        self.assertEqual(after["batches"], before["batches"])
        self.assertEqual(after["documents"], before["documents"])
        self.assertEqual(after["stage"], "MATCH")
        self.assertEqual(set(self.w.packet(rid)["input"]["records"]), {KEYS[1]})
        self.assertNotEqual(after["results"][KEYS[1]]["class"], "排除")

    def test_lookup_and_panorama_skip_semantic_classification(self):
        lookup = self.w.create({"entry": "lookup", "keys": [KEYS[0]], "read_scope": "biblio", "caller": "cnnv",
                                "approved_sources": ["patsnap_web"], "approved_models": ["codex"]})
        rid = lookup["run_id"]
        path = self.export_csv([[KEYS[0], "2021-01-01", "2022-01-01", "Synthetic lookup", "有效", now()[:10]]])
        result = self.w.import_file(rid, path, query="Qlookup")
        self.assertEqual(result["stage"], "EXPORT")
        evidence = json.loads(Path(self.w.export(rid)["evidence"]).read_text(encoding="utf-8"))
        self.assertIsNone(evidence["docs"][0]["class"])
        self.assertEqual(result["matches"], {})
        panorama = self.start(intent="全景", hard_features=False)
        result = self.w.import_file(panorama, path, query="Q1")
        self.assertEqual(result["status"], "WAITING_IMPORT")
        self.add_pdf(panorama, KEYS[0], scope="claims")
        result = self.w.view(panorama)
        self.assertEqual(result["stage"], "EXPORT")
        evidence = json.loads(Path(self.w.export(panorama)["evidence"]).read_text(encoding="utf-8"))
        self.assertIsNone(evidence["docs"][0]["class"])
        self.assertEqual(result["matches"], {})

    def test_infringement_route_never_performs_feature_matching(self):
        rid = self.start(intent="侵权风险", hard_features=False)
        path = self.export_csv([[KEYS[0], "2021-01-01", "2022-01-01", "Synthetic risk", "有效", now()[:10]]])
        self.w.import_file(rid, path, query="Q1")
        self.add_pdf(rid, KEYS[0], scope="claims")
        run = self.w.view(rid)
        self.assertEqual(run["stage"], "EXPORT")
        self.assertEqual(run["results"][KEYS[0]]["class"], "待核实")
        self.assertEqual(run["matches"], {})
        actions = [trace["action"] for trace in self.w.store.traces(rid)]
        self.assertNotIn("AGENT_MATCH", actions)
        self.assertNotIn("AGENT_REVIEW", actions)

    def test_same_context_review_is_rejected(self):
        rid = self.populated()
        self.submit(rid, {"action": "stop", "reason": "进入独立复核验收。", "queries": [], "keys": []})
        packet = self.w.packet(rid)
        self.assertIn(KEYS[0], packet["input"]["review_keys"])
        review = {"model": "codex", "context_id": "match-context",
                  "items": [{"key": key, "verdict": "confirm", "reason": "自审不应被接受。"} for key in packet["input"]["review_keys"]]}
        with self.assertRaises(ContractError):
            self.submit(rid, review, context="match-context")
        self.assertEqual(self.w.view(rid)["reviews"], {})

    def test_pdf_mismatched_kind_and_missing_pages_cannot_be_confirmed(self):
        rid = self.start()
        self.w.import_file(rid, self.export_csv(), query="Q1")
        self.add_pdf(rid, KEYS[0], pages=[KEYS[0][:-1] + "B\n[0001] Wrong patent version."], confirm=False)
        with self.assertRaises(ContractError):
            self.w.confirm_pdf(rid, KEYS[0], "验收员", 1)
        self.add_pdf(rid, KEYS[2], pages=[KEYS[2] + "\nPage 1 of 3", "Page 3 of 3"], confirm=False)
        with self.assertRaises(ContractError):
            self.w.confirm_pdf(rid, KEYS[2], "验收员", 2)

    def test_stale_agent_packet_and_unapproved_egress_are_rejected(self):
        run = self.w.create({"raw_input": "未授权的未公开方案"})
        with self.assertRaises(ContractError):
            self.w.packet(run["run_id"])
        rid = self.start()
        self.w.import_file(rid, self.export_csv(), query="Q1")
        packet = self.w.packet(rid)
        self.add_pdf(rid, KEYS[0])
        with self.assertRaises(ContractError):
            self.w.submit(rid, "MATCH", {"task_token": packet["task_token"], "model": "codex", "context_id": "old",
                                        "elapsed_seconds": 1, "output": {"items": []}})

    def test_user_modified_export_is_preserved(self):
        rid = self.populated()
        self.finish(rid)
        directory = self.w.store.run_dir(rid)
        before = {name: (directory / name).read_bytes() for name in ("evidence.json", "evidence_matrix.csv", "trace.json")}
        report = directory / "report.html"
        original = report.read_text(encoding="utf-8")
        manual = original + "\n<!-- User's saved change must remain. -->\n"
        report.write_text(manual, encoding="utf-8", newline="")
        # A changed export payload makes an early evidence.json write observable,
        # even when both attempts occur within the same wall-clock second.
        run = self.w.store.load(rid)
        run["termination_reason"] = "测试新的导出内容，但不得在发现用户报告修改前写入任何交付文件。"
        self.w.store.save(run, "ACCEPTANCE_EXPORT_CONTENT_CHANGE", {})
        with self.assertRaises(ValueError):
            self.w.export(rid)
        self.assertEqual(report.read_text(encoding="utf-8"), manual)
        for name, content in before.items():
            self.assertEqual((directory / name).read_bytes(), content, name)

    def test_gap_evidence_reopens_matching_for_an_already_complete_pdf(self):
        rid = self.populated()
        before = self.w.view(rid)
        self.assertTrue(before["documents"][KEYS[0]]["quality"]["complete"])
        after = self.submit(rid, {"action": "evidence", "reason": "复读已经完整的全文，重新核对联合条件。",
                                  "queries": [], "keys": [KEYS[0]]})
        self.assertEqual(after["stage"], "MATCH")
        self.assertEqual(after["status"], "WAITING_AGENT")
        self.assertEqual(after["round"], 1)
        self.assertEqual(after["pending_evidence"], [])
        self.assertNotIn(KEYS[0], after["matches"])
        self.assertEqual(after["documents"], before["documents"])
        self.assertEqual(after["matches"], {key: item for key, item in before["matches"].items() if key != KEYS[0]})
        self.assertEqual(set(self.w.packet(rid)["input"]["records"]), {KEYS[0]})
        self.assertEqual(self.fill_matches(rid)["stage"], "GAP")

    def test_gap_evidence_accepts_confirmed_claims_scope_without_full_document(self):
        rid = self.start(read_scope="claims")
        path = self.export_csv([[KEYS[0], "2021-01-01", "2022-01-01", "Synthetic claims text", "有效", now()[:10]]])
        self.w.import_file(rid, path, query="Q1")
        self.add_pdf(rid, KEYS[0], pages=[KEYS[0] + "\n1. " + " ".join(QUOTES.values())], scope="claims")
        document = self.w.view(rid)["documents"][KEYS[0]]
        self.assertFalse(document["quality"]["complete"])
        self.assertTrue(document["quality"]["claims_complete"])
        output = {"items": [{"key": KEYS[0], "document_sha256": document["sha256"],
                            "features": [{"fid": fid, "state": "有支持", "quote": quote, "page": 1,
                                          "loc": "权利要求 1", "scheme": "权利要求 1", "read": "权利要求"}
                                         for fid, quote in QUOTES.items()]}]}
        self.assertEqual(self.submit(rid, deepcopy(output), context="claims-initial")["stage"], "GAP")
        after = self.submit(rid, {"action": "evidence", "reason": "按调用范围重新核对完整权利要求。",
                                  "queries": [], "keys": [KEYS[0]]})
        self.assertEqual(after["stage"], "MATCH")
        self.assertEqual(after["status"], "WAITING_AGENT")
        self.assertEqual(after["pending_evidence"], [])
        self.assertNotIn(KEYS[0], after["matches"])
        self.assertEqual(self.submit(rid, deepcopy(output), context="claims-reread")["stage"], "GAP")

    def test_gap_retrieval_returns_to_plan_appends_queries_and_stops_after_three_rounds(self):
        rid = self.start()
        self.w.import_file(rid, query="Q1", status="zero")
        original_spec = deepcopy(self.w.view(rid)["spec"])
        for number in range(1, 4):
            gap = {"action": "retrieve", "reason": f"第 {number} 轮增加气体传感器表达，保持规格不变。",
                   "queries": [], "keys": []}
            before = deepcopy(self.w.view(rid)["queries"])
            run = self.submit(rid, gap)
            self.assertEqual(run["stage"], "PLAN")
            self.assertEqual(run["round"], number)
            self.assertEqual(run["queries"], before)
            self.assertEqual(self.w.packet(rid)["input"]["gap_request"], gap)
            query = {"qid": f"Q{number + 1}", "query": f"battery AND gas detector term{number}",
                     "prompt": f"补充检索气体检测技术，第 {number} 轮。", "fids": ["F1"],
                     "required": True, "source": "patsnap_web"}
            if number == 1:
                duplicate = {**query, "qid": "Q1"}
                with self.assertRaises(ContractError):
                    self.submit(rid, {"queries": [duplicate]})
                self.assertEqual(self.w.view(rid)["queries"], before)
            run = self.submit(rid, {"queries": [query]})
            self.assertEqual(run["stage"], "RETRIEVE")
            self.assertEqual(run["queries"][:-1], before)
            self.assertEqual(run["queries"][-1]["qid"], query["qid"])
            self.assertEqual(run["spec"], original_spec)
            run = self.w.import_file(rid, query=query["qid"], status="zero")
            self.assertEqual(run["stage"], "GAP")
        before = self.w.view(rid)
        with self.assertRaises(ContractError):
            self.submit(rid, {"action": "retrieve", "reason": "第四轮应被程序拒绝。", "queries": [], "keys": []})
        after = self.w.view(rid)
        self.assertEqual(after["round"], 3)
        self.assertEqual(after["spec"], original_spec)
        self.assertEqual(after["queries"], before["queries"])
        self.assertEqual(after["_revision"], before["_revision"])

    def test_changed_pdf_bytes_reject_old_packet_confirmation_and_export(self):
        rid = self.populated()
        self.submit(rid, {"action": "stop", "reason": "进入独立复核。", "queries": [], "keys": []})
        packet = self.w.packet(rid)
        before = self.w.view(rid)
        old_hash = before["documents"][KEYS[0]]["sha256"]
        path = Path(before["documents"][KEYS[0]]["path"])
        make_pdf(path, [KEYS[0] + "\n[0001] Replacement text differs from the previously matched PDF."])
        with self.assertRaises(ContractError):
            self.w.packet(rid)
        with self.assertRaises(ContractError):
            self.w.confirm_pdf(rid, KEYS[0], "验收员", 1)
        review = {"model": "codex", "context_id": "review-old-pdf",
                  "items": [{"key": key, "verdict": "confirm", "reason": "旧任务包应拒绝。"} for key in packet["input"]["review_keys"]]}
        with self.assertRaises(ContractError):
            self.w.submit(rid, "REVIEW", {"task_token": packet["task_token"], "model": "codex",
                                         "context_id": "review-old-pdf", "elapsed_seconds": 1, "output": review})
        self.w.stop(rid, "尝试交付时也应发现 PDF 内容变化。")
        with self.assertRaises(ContractError):
            self.w.export(rid)
        self.assertFalse((self.w.store.run_dir(rid) / "evidence.json").exists())
        self.assertEqual(self.w.view(rid)["documents"][KEYS[0]]["sha256"], old_hash)
        replacement = self.w.replace_pdf(rid, KEYS[0], path)
        self.assertNotEqual(replacement["documents"][KEYS[0]]["sha256"], old_hash)
        self.assertFalse(replacement["documents"][KEYS[0]]["quality"]["complete"])
        self.assertNotIn(KEYS[0], replacement["matches"])
        self.assertNotIn(KEYS[0], replacement["reviews"])
        self.assertEqual(replacement["stage"], "MATCH")

    def test_disputed_review_allows_only_two_corrections_then_exports_pending(self):
        rid = self.populated()
        for cycle in range(3):
            self.submit(rid, {"action": "stop", "reason": "进入独立复核，检查争议处理上限。", "queries": [], "keys": []})
            packet = self.w.packet(rid)
            self.assertIn(KEYS[0], packet["input"]["review_keys"])
            context = f"review-dispute-{cycle}"
            review = {"model": "codex", "context_id": context,
                      "items": [{"key": key, "verdict": "revise" if key == KEYS[0] else "confirm",
                                 "reason": "F3 联合条件的语义仍存在争议，须重新取证。" if key == KEYS[0] else "日期排除已核对。"}
                                for key in packet["input"]["review_keys"]]}
            run = self.submit(rid, review, context=context)
            self.assertEqual(run["corrections"], min(cycle + 1, 2))
            if cycle < 2:
                self.assertEqual(run["stage"], "MATCH")
                self.assertNotIn(KEYS[0], run["matches"])
                self.assertEqual(set(self.w.packet(rid)["input"]["records"]), {KEYS[0]})
                self.assertEqual(self.fill_matches(rid)["stage"], "GAP")
            else:
                self.assertEqual(run["stage"], "EXPORT")
        evidence = json.loads(Path(self.w.export(rid)["evidence"]).read_text(encoding="utf-8"))
        docs = {doc["key"]: doc for doc in evidence["docs"]}
        self.assertEqual(docs[KEYS[0]]["class"], "待核实")
        self.assertEqual(docs[KEYS[1]]["class"], "排除")
        self.assertFalse(evidence["review_complete"])
        self.assertEqual(evidence["status"], "PARTIAL")
        self.assertTrue(any("2 轮" in issue for issue in evidence["needs_human"]))
        actions = [trace["action"] for trace in self.w.store.traces(rid)]
        self.assertEqual(actions.count("AGENT_MATCH"), 3)
        self.assertEqual(actions.count("AGENT_REVIEW"), 3)


if __name__ == "__main__":
    unittest.main()
