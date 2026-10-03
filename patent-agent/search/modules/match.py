"""Validate Agent evidence; never create a semantic judgment from keywords.

The Agent supplies one of six Chinese evidence states and exact PDF citations.
This module verifies identity, version, reading scope, location and scheme, then
evaluates the existing hard tree. It does not decide whether a quote entails a
technical feature. Independent semantic review remains an Agent responsibility.
"""

import re
from collections import defaultdict

from shared.rules import FAIL, PASS, UNKNOWN, conjunction, disjunction, evaluate, feature_ids
from .filter import assess as filter_assess

EVIDENCE_VALUES = {"有支持": PASS, "明确相反": FAIL, "部分支持": UNKNOWN,
                   "未找到": UNKNOWN, "材料不可得": UNKNOWN, "来源冲突": UNKNOWN}
READ_LEVELS = {"摘要": 0, "权利要求": 1, "全文": 2}


def _location_contains(text, loc, quote):
    """A page number alone is insufficient; check the claimed text segment."""
    if not isinstance(loc, str):
        return False
    paragraph = re.search(r"[\[【（(](\d{4,})[\]】）)]", loc)
    if paragraph:
        token = paragraph.group(1)
        markers = list(re.finditer(r"[\[【（(](\d{4,})[\]】）)]", text))
        for index, marker in enumerate(markers):
            if marker.group(1) == token:
                end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                if quote in text[marker.start():end]:
                    return True
        return False
    claim = re.search(r"(?:权利要求|claim\s*)\s*(\d+)", loc, re.I)
    if not claim:
        return False
    number = int(claim.group(1))
    markers = list(re.finditer(r"(?:^|\n)\s*(\d+)\s*[.．、]\s*", text))
    for index, marker in enumerate(markers):
        if int(marker.group(1)) == number:
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            if quote in text[marker.start():end]:
                return True
    return False


def _global_issues(record, spec, document, submission, hard):
    issues = []
    if not isinstance(document, dict):
        return ["PDF 全文材料不可得。"]
    quality = document.get("quality") or {}
    if document.get("key") != record.get("key"):
        issues.append("PDF 公开号或种类码与候选记录不一致。")
    if quality.get("identity_verified") is not True:
        issues.append("PDF 公开文本身份尚未核实。")
    if quality.get("text_available") is not True or quality.get("needs_ocr"):
        issues.append("PDF 文字不可完整读取，需补取可读材料或核实 OCR。")
    scope = spec.get("read_scope", "full")
    if scope == "full" and quality.get("complete") is not True:
        issues.append("全文完整性尚未确认，不能据此作肯定或技术否定判定。")
    if scope == "claims" and not (quality.get("claims_complete") is True or quality.get("complete") is True):
        issues.append("权利要求完整性尚未确认，不能据此作肯定或技术否定判定。")
    if scope == "biblio":
        issues.append("实际请求仅取著录与摘要，不能定案。")
    if not isinstance(submission, dict):
        issues.append("尚未收到 Agent 逐特征证据。")
        return issues
    if submission.get("key") != record.get("key"):
        issues.append("证据提交的公开号或种类码与候选不一致。")
    if not document.get("sha256") or submission.get("document_sha256") != document.get("sha256"):
        issues.append("证据提交与当前 PDF 的 SHA-256 不一致，须重新取证。")
    if not submission.get("model") or not submission.get("context_id"):
        issues.append("证据提交缺少模型或上下文标识。")
    return issues


def _validate_item(item, spec, document, global_issues):
    result = dict(item)
    issues = list(global_issues)
    state = item.get("state")
    value = EVIDENCE_VALUES.get(state, UNKNOWN)
    if state not in EVIDENCE_VALUES:
        issues.append("证据状态不在约定的六种状态中。")
    if value in (PASS, FAIL):
        quote = item.get("quote")
        page = item.get("page")
        scheme = item.get("scheme")
        if not isinstance(scheme, str) or not scheme.strip():
            issues.append("未标明同一技术方案标识。")
        read = item.get("read")
        required_level = 2 if spec.get("read_scope", "full") == "full" else 1
        if READ_LEVELS.get(read, -1) < required_level:
            issues.append("实际阅读深度不足；摘要不能作支持或明确相反证据。")
        if read == "全文" and (document or {}).get("quality", {}).get("complete") is not True:
            issues.append("实际阅读声明为全文，但全文完整性未确认。")
        if spec.get("read_scope", "full") == "claims" and not re.search(r"(?:权利要求|claim\s*)\s*\d+", str(item.get("loc", "")), re.I):
            issues.append("本次仅比对权利要求，不能以说明书段落替代。")
        if not isinstance(quote, str) or not quote.strip():
            issues.append("缺少原文逐字引文。")
        elif not isinstance(page, int) or isinstance(page, bool) or page < 1:
            issues.append("缺少有效 PDF 页码。")
        else:
            pages = (document or {}).get("pages", [])
            matching_pages = [entry for entry in pages if entry.get("page") == page]
            text = matching_pages[0].get("text", "") if len(matching_pages) == 1 else ""
            if quote not in text:
                issues.append("逐字引文不在指定 PDF 页中。")
            elif not _location_contains(text, item.get("loc"), quote):
                issues.append("引文未定位到指定权利要求或说明书段落。")
    result.update(value=UNKNOWN if issues else value, issues=issues)
    return result


def assess(record, spec, document=None, submission=None):
    """Return a deterministic classification with verified evidence and reasons.

    Fields can exclude without a PDF. Technical evaluation is performed for
    each scheme independently, so AND/OR/NOT cannot splice different examples.
    A valid positive scheme is sufficient; exclusion requires FAIL for every
    represented scheme, with UNKNOWN retained whenever evidence is missing.
    """
    filtered = filter_assess(record, spec)
    output = {"value": filtered["value"], "class": None, "branch": filtered["branch"],
              "evidence": [], "issues": [], "reasons": list(filtered["reasons"])}
    if spec.get("entry", "search") == "lookup" or spec.get("intent") == "全景":
        return output
    if filtered["value"] == FAIL:
        output["class"] = "排除"
        return output
    if spec.get("intent") == "侵权风险":
        output["class"] = "待核实"
        output["reasons"].append("侵权风险入口不执行语义匹配；独立权利要求交后续流程逐特征比对。")
        return output
    hard = spec.get("hard", {"op": "AND", "args": []})
    required = feature_ids(hard)
    known = {feature["fid"] for feature in spec.get("features", []) if isinstance(feature, dict) and "fid" in feature}
    if required - known:
        output["issues"].append("硬条件引用了未定义的技术特征。")
    items = submission.get("features", []) if isinstance(submission, dict) else []
    if not isinstance(items, list):
        items = []
    global_issues = _global_issues(record, spec, document, submission, hard) if required else []
    verified = []
    for item in items:
        if isinstance(item, dict) and item.get("fid") in known:
            verified.append(_validate_item(item, spec, document, global_issues))
    covered = {item["fid"] for item in verified}
    for fid in sorted(known - covered):
        verified.append({"fid": fid, "state": "材料不可得", "quote": None, "page": None,
                         "loc": None, "scheme": None, "read": None, "value": UNKNOWN,
                         "issues": ["未提交此特征的证据。"]})
    output["evidence"] = verified
    output["issues"].extend(global_issues)
    for item in verified:
        output["issues"].extend(f"{item['fid']}: {issue}" for issue in item["issues"] if issue not in global_issues)
    by_scheme = defaultdict(lambda: defaultdict(list))
    for item in verified:
        if item.get("fid") in required:
            scheme = item.get("scheme") if isinstance(item.get("scheme"), str) else ""
            by_scheme[scheme][item["fid"]].append(item["value"])
    # No technical leaves means a field-only classification needs no PDF.
    if not required:
        hard_value = evaluate(hard, record)
    else:
        scheme_values = []
        for scheme, features in by_scheme.items():
            values = {}
            for fid in required:
                states = features.get(fid, [UNKNOWN])
                values[fid] = states[0] if len(set(states)) == 1 and scheme and fid in known else UNKNOWN
                if len(set(states)) > 1:
                    output["issues"].append(f"{fid} 在同一技术方案中的证据状态冲突。")
            scheme_values.append(evaluate(hard, record, values))
        hard_value = disjunction(scheme_values) if scheme_values else evaluate(hard, record)
        if hard_value == UNKNOWN and len([scheme for scheme in by_scheme if scheme]) > 1:
            output["issues"].append("不同技术方案的特征不得拼接为同一方案。")
    value = conjunction((hard_value, filtered.get("intent_value", UNKNOWN)))
    output["value"] = value
    if value == FAIL:
        output["class"] = "排除"
        output["reasons"].append("核验后的硬条件逻辑树为 FAIL。")
    elif value == PASS and output["branch"] != "抵触申请":
        output["class"] = "匹配"
        output["reasons"].append("硬条件全部满足；技术证据仍须独立语义复核。")
    else:
        output["class"] = "待核实"
        output["reasons"].append("存在待核实条件或属于抵触申请候选。")
    output["issues"] = list(dict.fromkeys(output["issues"]))
    return output
