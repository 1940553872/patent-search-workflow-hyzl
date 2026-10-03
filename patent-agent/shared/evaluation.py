"""Calculate acceptance metrics from one export and its human labels.

This function is read-only. It runs no patent search or experiment, never fills
missing human labels, and never writes calculated metrics into the evidence.
"""

from collections import Counter
import math
from statistics import NormalDist


TARGETS = {
    "required_query_execution": {"operator": "eq", "value": 1.0},
    "precision": {"operator": "gte", "value": 0.85},
    "false_exclusion_rate": {"operator": "eq", "value": 0.0},
    "evidence_location_completeness": {"operator": "eq", "value": 1.0},
    "conservation_difference": {"operator": "eq", "value": 0},
}


def _metric(key, name, numerator, denominator, *, value=None, sample_size=None):
    if value is None and denominator:
        value = numerator / denominator
    target = dict(TARGETS[key])
    if value is None:
        assessment = "未评测"
    else:
        passed = value >= target["value"] if target["operator"] == "gte" else value == target["value"]
        assessment = "通过" if passed else "未达标"
    return {"name": name, "value": value,
            "sample_size": sample_size if sample_size is not None else denominator,
            "numerator": numerator, "denominator": denominator,
            "target": target, "assessment": assessment}


def _wilson(correct, total):
    if total == 0:
        return None
    z = NormalDist().inv_cdf(0.975)
    rate, z2 = correct / total, z * z
    denominator = 1 + z2 / total
    center = (rate + z2 / (2 * total)) / denominator
    half = z * math.sqrt(rate * (1 - rate) / total + z2 / (4 * total * total)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def _validated_labels(evidence, labels):
    if not isinstance(evidence, dict) or not isinstance(labels, dict):
        raise ValueError("evidence 和 labels 必须为 JSON 对象")
    for identity in ("query_id", "snapshot"):
        if not evidence.get(identity) or labels.get(identity) != evidence[identity]:
            raise ValueError(f"人工标注的 {identity} 与证据快照不一致")
    docs = evidence.get("docs")
    if not isinstance(docs, list):
        raise ValueError("证据缺少 docs 清单")
    by_key = {}
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("key"), str) or not doc["key"]:
            raise ValueError("证据文献缺少有效 key")
        if doc["key"] in by_key:
            raise ValueError("证据含重复文献 key")
        if doc.get("class") not in ("匹配", "排除", "待核实", None):
            raise ValueError("证据含未约定的文献分类")
        by_key[doc["key"]] = doc
    items = labels.get("items")
    if not isinstance(items, list):
        raise ValueError("人工标注 items 必须为列表；未标注时请明确提供空列表")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"key", "expected_relevant", "match_correct"}:
            raise ValueError("人工标注条目字段无效")
        key = item.get("key")
        if not isinstance(key, str) or key not in by_key:
            raise ValueError(f"人工标注引用未知文献：{key}")
        if key in seen:
            raise ValueError(f"人工标注文献重复：{key}")
        seen.add(key)
        for field in ("expected_relevant", "match_correct"):
            if field in item and type(item[field]) is not bool:
                raise ValueError(f"{field} 必须为 true 或 false；缺失时省略字段")
    return by_key, items


def evaluate(evidence: dict, labels: dict) -> dict:
    """Return five metrics, sample sizes, acceptance decisions and provenance.

    Precision uses only human-labeled documents actually classified as matches.
    The Wilson 95% interval describes sampling uncertainty; the 0.85 target is
    applied to the measured proportion, not to the interval's lower bound.
    Missing labels or zero denominators produce 未评测, never a default pass.
    """
    by_key, items = _validated_labels(evidence, labels)
    notes = ["人工标注仅覆盖所列文献；指标不代表全库查全率。",
             "Wilson 95% 区间表示匹配精确率在有限抽检样本下的不确定范围。"]
    queries = evidence.get("scope", {}).get("queries", [])
    required = [query for query in queries if query.get("required") is True]
    executed = sum(query.get("status") in ("complete", "zero") for query in required)
    precision_items = [item for item in items if "match_correct" in item and by_key[item["key"]].get("class") == "匹配"]
    correct = sum(item["match_correct"] for item in precision_items)
    relevant = [item for item in items if item.get("expected_relevant") is True]
    removed = sum(by_key[item["key"]].get("class") == "排除" for item in relevant)
    ignored = sum("match_correct" in item and by_key[item["key"]].get("class") != "匹配" for item in items)
    if ignored:
        notes.append(f"{ignored} 条 match_correct 标注对应非匹配文献，未纳入匹配精确率。")
    metrics = {
        "required_query_execution": _metric("required_query_execution", "必需查询执行率", executed, len(required)),
        "precision": _metric("precision", "匹配精确率", correct, len(precision_items)),
        "false_exclusion_rate": _metric("false_exclusion_rate", "误删率", removed, len(relevant)),
    }
    metrics["precision"]["wilson_interval"] = _wilson(correct, len(precision_items))
    metrics["precision"]["confidence_level"] = 0.95

    # Retain the export metric, but independently check that it counted only
    # evidence that passed the deterministic location/version verifier.
    cells = [(doc, cell) for doc in by_key.values() for cell in doc.get("evidence", [])
             if cell.get("state") in ("有支持", "明确相反")]
    expected_values = {"有支持": "PASS", "明确相反": "FAIL"}
    verified = sum(bool(cell.get("quote") and cell.get("loc")
                        and type(cell.get("page")) is int and cell["page"] > 0
                        and doc.get("document", {}).get("sha256")
                        and cell.get("value") == expected_values[cell["state"]]
                        and not cell.get("issues")) for doc, cell in cells)
    declared = evidence.get("metrics", {}).get("evidence_location_completeness")
    declared_valid = type(declared) in (int, float) and math.isfinite(declared) and 0 <= declared <= 1
    location = _metric("evidence_location_completeness", "证据定位完整率", verified, len(cells))
    location["exported_value"] = declared
    if not declared_valid or not cells:
        location.update(value=None, assessment="未评测")
        notes.append("原证据未提供有效定位完整率或无待定位证据，不将缺失指标判为通过。")
    elif not math.isclose(declared, verified / len(cells), rel_tol=0, abs_tol=1e-12):
        location["assessment"] = "未达标"
        notes.append("原证据定位指标与核验结果不一致，已按核验结果列值并判为未达标。")
    metrics["evidence_location_completeness"] = location

    counts = evidence.get("counts", {})
    candidates = counts.get("candidates")
    categories = ("matched", "excluded", "pending", "unclassified", "hard_excluded_unclassified")
    difference = None
    consistent = True
    if type(candidates) is int and candidates >= 0 and all(type(counts.get(key)) is int and counts[key] >= 0 for key in categories):
        difference = candidates - sum(counts[key] for key in categories)
        actual_counts = Counter(doc.get("class") for doc in by_key.values())
        consistent = all(counts[name] == actual_counts[label] for name, label in
                         (("matched", "匹配"), ("excluded", "排除"), ("pending", "待核实"), ("unclassified", None)))
        excluded_keys = evidence.get("scope", {}).get("hard_excluded_keys", [])
        consistent = consistent and counts["hard_excluded_unclassified"] == len(set(excluded_keys)) and not set(excluded_keys).intersection(by_key)
    conservation = _metric("conservation_difference", "数量守恒差", None, None, value=difference,
                           sample_size=candidates if type(candidates) is int else None)
    conservation["counts_consistent_with_documents"] = consistent if difference is not None else None
    if difference is not None and not consistent:
        conservation["assessment"] = "未达标"
        notes.append("计数与实际文献清单不一致，即使总数差为零也不能通过数量守恒验收。")
    metrics["conservation_difference"] = conservation
    decisions = [metric["assessment"] for metric in metrics.values()]
    status = "FAIL" if "未达标" in decisions else "NOT_EVALUATED" if "未评测" in decisions else "PASS"
    return {"query_id": evidence["query_id"], "snapshot": evidence["snapshot"], "status": status,
            "metrics": metrics, "targets": {key: dict(value) for key, value in TARGETS.items()},
            "sample_sizes": {"label_items": len(items), "matched_labeled": len(precision_items),
                             "relevant_labeled": len(relevant), "required_queries": len(required),
                             "evidence_cells": len(cells), "candidates": candidates}, "notes": notes}
