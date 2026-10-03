"""Export a fixed run snapshot. No model judgment is made here."""
from __future__ import annotations
import copy
import csv
import html
import io
from collections import Counter

from shared.storage import now
from shared.rules import evaluate


def build_export(run, review_targets, missing_materials):
    spec = run["spec"]
    classified = spec["entry"] == "search" and spec["intent"] != "全景"
    review_required = spec["entry"] == "search" and spec["intent"] == "新颖性"
    docs = []
    hard_excluded = 0
    human = list(run["needs_human"])
    for key, record in run["records"].items():
        result = run["results"].get(key, {})
        if spec["intent"] == "全景" and result.get("hard_value") == "FAIL":
            hard_excluded += 1
            continue
        review = run["reviews"].get(key)
        label = result.get("class", "待核实") if classified else None
        if review_required:
            if label == "匹配" and (not review or review["verdict"] != "confirm"):
                label = "待核实"
            if label == "排除" and result.get("hard_value") != "FAIL" and (not review or review["verdict"] != "confirm"):
                label = "待核实"
            if review and review["verdict"] == "revise":
                label = "待核实"
        doc = copy.deepcopy(record)
        doc.update({"class": label, "branch": result.get("branch"), "evidence": result.get("evidence", []),
                    "decision": result, "review": review, "claims": record.get("claims", [])})
        # Relationships link public texts without merging their evidence or classifications.
        doc["relations"] = {
            "same_application": sorted(k for k, r in run["records"].items() if k != key and record.get("application_number") and r.get("application_number") == record["application_number"]),
            "same_family": sorted(k for k, r in run["records"].items() if k != key and record.get("family_id") and r.get("family_id") == record["family_id"])}
        feature_values = {c["fid"]: c.get("value", "UNKNOWN") for c in doc["evidence"]}
        preference_values = [evaluate(s, record, feature_values) if isinstance(s, dict) else "UNKNOWN" for s in spec["soft"]]
        doc["preferences"] = {"values": preference_values, "satisfied": preference_values.count("PASS"), "unknown": preference_values.count("UNKNOWN")}
        document = run["documents"].get(key)
        if document:
            doc["document"] = {"sha256": document["sha256"], "path": document["path"], "quality": document["quality"],
                               "page_count": len(document["pages"])}
        if spec["intent"] == "侵权风险":
            doc["independent_claims"] = [c for c in doc["claims"] if c.get("independent") is True]
            if not doc["independent_claims"] and label != "排除":
                human.append(f"{key} 的独立权利要求尚未确认；已保留全部取得的权利要求供调用方复核")
        docs.append(doc)
    docs.sort(key=lambda d: (-d["preferences"]["satisfied"], d["key"]))
    required = [q for q in run["queries"] if q["required"]]
    executed = [q for q in required if q["status"] in ("complete", "zero")]
    coverage = len(executed) == len(required) and not run["force_partial"] and not run["budget_exhausted"]
    needs_import = [{"query_id": q["qid"], "query": q["query"], "prompt": q["prompt"], "status": q["status"],
                     "fields": ["公开号", "申请号", "申请日", "优先权日", "公开日", "法律状态", "法律状态日期", "摘要"]}
                    for q in required if q["status"] not in ("complete", "zero")]
    if missing_materials:
        needs_import.append({"pdf_keys": missing_materials, "folder": "pdf_inbox", "read_scope": spec["read_scope"]})
    review_complete = not review_required or all(run["reviews"].get(k, {}).get("verdict") == "confirm" for k in review_targets)
    screening = all(d["class"] in ("匹配", "排除", "待核实") for d in docs) if classified else not missing_materials
    counts = {"candidates": len(run["records"]), "matched": 0, "excluded": 0, "pending": 0,
              "unclassified": 0, "hard_excluded_unclassified": hard_excluded}
    for doc in docs:
        counts[{"匹配": "matched", "排除": "excluded", "待核实": "pending", None: "unclassified"}[doc["class"]]] += 1
    conservation = counts["candidates"] - sum(counts[k] for k in ("matched", "excluded", "pending", "unclassified", "hard_excluded_unclassified"))
    if conservation != 0:
        raise ValueError("数量不守恒，已停止导出")
    raw = sum(b["raw_rows"] for b in run["batches"])
    omitted = sum(b.get("omitted_rows", 0) for b in run["batches"])
    hard_fail = sum(r.get("hard_value") == "FAIL" for r in run["results"].values())
    n = counts["candidates"]
    match_count = sum(bool(run["matches"].get(k)) for k in run["records"])
    reasons = Counter()
    for r in run["results"].values():
        if r.get("hard_value") == "FAIL":
            reasons["；".join(r.get("reasons", [])) or "硬条件不满足"] += 1
    funnel = [
        {"stage": "召回", "in": raw, "out": raw, "reasons": {}},
        {"stage": "去重", "in": raw, "out": n, "reasons": {"重复导出行": max(0, raw - n - omitted), "超上限未纳入行": omitted}},
        {"stage": "硬条件", "in": n, "out": n - hard_fail, "reasons": dict(reasons)},
        {"stage": "取证", "in": n - hard_fail, "out": n - hard_fail,
         "reasons": {"已提交取证": match_count, "未提交取证": max(0, n - hard_fail - match_count)}},
        {"stage": "复核", "in": len(review_targets), "out": sum(run["reviews"].get(k, {}).get("verdict") == "confirm" for k in review_targets),
         "reasons": {"待复核或有争议": sum(run["reviews"].get(k, {}).get("verdict") != "confirm" for k in review_targets)}}
    ]
    cells = [c for d in docs for c in d["evidence"] if c.get("state") in ("有支持", "明确相反")]
    located = sum(bool(c.get("quote") and c.get("loc") and c.get("page") and d.get("document", {}).get("sha256")
                       and c.get("value") in ("PASS", "FAIL") and not c.get("issues"))
                  for d in docs for c in d["evidence"] if c.get("state") in ("有支持", "明确相反"))
    blind = "发明申请通常自申请日起满 18 个月公布；提前公布或保密等情形另有规定。尚未公开申请不能通过公开数据库检出。"
    if missing_materials:
        human.append("部分全文或权利要求未取得/未确认完整，参见 import_todo")
    status = "COMPLETE" if coverage and screening and review_complete and not run["force_partial"] else "PARTIAL"
    if run["status"] == "WAITING_IMPORT" and not run.get("termination_reason"):
        status = "PENDING_IMPORT"
    return {"request": {**run["request"], "features": spec["features"], "base_date": spec["base_date"]},
            "query_id": run["run_id"], "snapshot": run["snapshot"], "docs": docs, "counts": counts, "funnel": funnel,
            "scope": {"sources": sorted({q["source"] for q in run["queries"]}), "queries": run["queries"],
                      "collection": "用户在智慧芽官网执行检索，程序导入导出文件；未调用数据库 API",
                      "snapshot_note": "snapshot 为本地任务快照日期，各批次实际导入时间见 batches.at；源库更新延迟未知",
                      "truncated": any(q["status"] == "truncated" for q in run["queries"]) or omitted > 0,
                      "batches": run["batches"], "blind_spot": blind, "read_scope": spec["read_scope"],
                      "coverage_note": "必需查询完成只表示本计划已执行，不代表全库查全",
                      "semantic_matching_applicable": review_required,
                      "independent_review_applicable": review_required,
                      "hard_excluded_keys": [k for k, r in run["results"].items() if spec["intent"] == "全景" and r.get("hard_value") == "FAIL"],
                      "gates": run["gates"]},
            "import_todo": needs_import, "status": status, "needs_human": sorted(set(human)),
            "coverage_complete": coverage, "screening_complete": screening, "review_complete": review_complete,
            "versions": {"files": run["versions"], "config": run["config"], "exported_at": now()},
            "termination_reason": run["termination_reason"] or "计划执行完成",
            "metrics": {"required_query_execution": len(executed) / len(required) if required else None,
                        "evidence_location_completeness": located / len(cells) if cells else None,
                        "conservation_difference": conservation, "precision": None, "wilson_interval": None,
                        "false_exclusion_rate": None, "note": "匹配精确率及误删率需要真实人工标注；当前不计算、不宣称达标"}}


def render_matrix(evidence):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["公开号", "分类", "日期分支", "特征", "Agent证据状态", "程序核验", "核验问题", "阅读深度", "引文", "位置", "页码", "同一方案", "PDF_SHA256"])
    for doc in evidence["docs"]:
        for cell in doc["evidence"] or [{}]:
            values = [doc["key"], doc["class"], doc["branch"], cell.get("fid"), cell.get("state"), cell.get("value"), "；".join(cell.get("issues", [])), cell.get("read"),
                      cell.get("quote"), cell.get("loc"), cell.get("page"), cell.get("scheme"), doc.get("document", {}).get("sha256")]
            # Spreadsheet applications must not execute a formula embedded in a title or quote.
            writer.writerow(["'" + str(v) if str(v or "").startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
    return output.getvalue()


def render_report(evidence):
    e = lambda x: html.escape(str(x if x is not None else "—"))
    counts = evidence["counts"]
    pieces = ["<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>专利检索结果</title>",
              "<style>body{font:16px/1.7 system-ui,sans-serif;max-width:1150px;margin:40px auto;padding:0 24px;color:#172c3c}h1{font-size:28px}h2{margin-top:32px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce3e8;vertical-align:top}th{background:#edf4f4}small{color:#52616c}blockquote{margin:12px 0;padding:12px 16px;background:#f3f7fa;white-space:pre-wrap}code{word-break:break-all} .card{border:1px solid #dce3e8;border-radius:10px;padding:20px;margin:20px 0}a{color:#007c78}</style>",
              f"<h1>专利检索与筛选结果</h1><p>{e(evidence['query_id'])} · 快照日期 {e(evidence['snapshot'])} · {e(evidence['status'])}</p>",
              f"<p>候选 {counts['candidates']}；匹配 {counts['matched']}；排除 {counts['excluded']}；待核实 {counts['pending']}；不分类 {counts['unclassified']}；全景硬条件剔除 {counts['hard_excluded_unclassified']}。</p>",
              f"<p>结束原因：{e(evidence['termination_reason'])}</p>",
              "<p>匹配表示满足本次检索条件，不代表已作新颖性或侵权法律判断。</p>",
              "<h2>完成范围</h2><table><tr><th>查询覆盖</th><th>逐篇归类</th><th>独立复核</th></tr>",
              "<tr>" + "".join(f"<td>{'不适用（按入口跳过）' if k == 'review_complete' and not evidence['scope']['independent_review_applicable'] else '完成' if evidence[k] else '未完成'}</td>" for k in ("coverage_complete", "screening_complete", "review_complete")) + "</tr></table>",
              f"<p>{e(evidence['scope']['collection'])}。{e(evidence['scope']['coverage_note'])}。</p>",
              f"<p>{e(evidence['scope']['blind_spot'])}</p>",
              "<h2>漏斗统计</h2><table><tr><th>阶段</th><th>进入</th><th>保留/完成</th><th>原因</th></tr>"]
    for f in evidence["funnel"]:
        if f["stage"] in ("取证", "复核") and not evidence["scope"]["semantic_matching_applicable"]:
            pieces.append(f"<tr><td>{e(f['stage'])}</td><td colspan='3'>不适用（按入口跳过）</td></tr>")
            continue
        pieces.append(f"<tr><td>{e(f['stage'])}</td><td>{f['in']}</td><td>{f['out']}</td><td>{e(f['reasons'])}</td></tr>")
    pieces.append("</table><h2>文献与证据</h2>")
    for doc in evidence["docs"]:
        pieces.append(f"<article class='card'><h3>{e(doc['key'])} · {e(doc.get('title'))}</h3><p>{e(doc['class'] or '不分类')} · {e(doc['branch'])}</p>")
        pieces.append(f"<small>申请日 {e(doc['dates'].get('app'))}；优先权日 {e(doc['dates'].get('pri'))}；公开日 {e(doc['dates'].get('pub'))}</small>")
        for cell in doc["evidence"]:
            pieces.append(f"<p><b>{e(cell.get('fid'))} · Agent 状态：{e(cell.get('state'))}</b> · 程序核验：{e(cell.get('value'))} · {e(cell.get('loc'))}，PDF 第 {e(cell.get('page'))} 页</p><blockquote>{e(cell.get('quote'))}</blockquote><p>{e('；'.join(cell.get('issues', [])))}</p>")
        pieces.append(f"<p>判定说明：{e(doc.get('decision', {}).get('reasons', []))}</p><p>复核：{e(doc.get('review'))}</p></article>")
    pieces.append("<h2>待处理事项</h2><ul>" + "".join(f"<li>{e(s)}</li>" for s in evidence["needs_human"]) + "</ul>")
    pieces.append("<h2>实际查询</h2><table><tr><th>编号</th><th>检索式</th><th>执行状态</th></tr>")
    for q in evidence["scope"]["queries"]:
        pieces.append(f"<tr><td>{e(q['qid'])}</td><td>{e(q['query'])}</td><td>{e(q['status'])}</td></tr>")
    pieces.append("</table><p><small>完整原始来源、证据和程序版本见同目录 evidence.json；运行记录见 trace.json。</small></p></html>")
    return "\n".join(pieces)
