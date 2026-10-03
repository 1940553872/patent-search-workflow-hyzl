"""Conservative patent identity normalization and provenance-preserving merges.

No network lookups, source mutation, legal inference, or A/B text merging occurs.
Conflicting values are retained with their sources and the usable field is empty.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import re
import unicodedata


ALIASES = {
    "publication_number": ("publication_number", "publication number", "公开号", "公布号", "公开(公告)号", "公开（公告）号", "公开公告号", "公开号/公告号", "公开/公告号", "专利号"),
    "title": ("title", "patent title", "标题", "名称", "专利名称", "发明名称", "专利标题"),
    "application_number": ("application_number", "application number", "申请号"),
    "family_id": ("family_id", "family id", "同族号", "同族ID", "简单同族", "同族编号"),
    "dates.app": ("application_date", "application date", "申请日", "申请日期"),
    "dates.pri": ("priority_date", "earliest priority date", "优先权日", "最早优先权日"),
    "dates.pub": ("publication_date", "publication date", "公开日", "公布日", "公开(公告)日", "公开（公告）日", "公开公告日", "公开/公告日"),
    "legal.state": ("legal_status", "legal state", "法律状态", "当前法律状态", "法律状态/事件"),
    "legal.expiry": ("expiry_date", "expiration_date", "到期日", "失效日", "届满日"),
    "legal.as_of": ("legal_as_of", "legal_status_date", "法律状态日期", "法律状态核验日", "法律状态更新日"),
    "applicants": ("applicants", "applicant", "申请人", "申请（专利权）人", "申请(专利权)人", "申请人/专利权人"),
    "ipc": ("ipc", "ipc分类号", "IPC分类", "IPC主分类", "分类号", "国际专利分类号"),
    "abstract": ("abstract", "摘要", "中文摘要"),
    "claims": ("claims", "权利要求", "权利要求书"),
}
FIELDS = tuple(ALIASES)
LIST_FIELDS = {"applicants", "ipc", "claims"}
DATE_FIELDS = {"dates.app", "dates.pri", "dates.pub", "legal.expiry", "legal.as_of"}


def _token(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold()


def parse_publication_number(value) -> tuple[str, str, str] | None:
    """Require authority, digits, and kind code; never infer missing components."""
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).upper().strip()
    text = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"([A-Z]{2})([0-9]{5,14})([A-Z][0-9]?)", text)
    return match.groups() if match else None


def _get(record, field):
    obj = record
    for key in field.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _put(record, field, value):
    obj = record
    keys = field.split(".")
    for key in keys[:-1]:
        obj = obj.setdefault(key, {})
    obj[keys[-1]] = value


def _empty(value):
    return value is None or value == "" or value == []


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # Dates are exact days.  Partial dates and Excel serial numbers stay unknown.
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|(?:T| )00:00:00)?", text)
    if not match:
        raise ValueError("日期必须包含完整年月日")
    return date(*(int(part) for part in match.groups())).isoformat()


def _normalize_value(field, value):
    if _empty(value):
        return None
    if field in DATE_FIELDS:
        return _date(value)
    if field == "publication_number":
        parsed = parse_publication_number(value)
        if not parsed:
            raise ValueError("公开号须明确包含公布机构、号码和种类码")
        return "".join(parsed)
    if field in {"applicants", "ipc"}:
        values = value if isinstance(value, list) else re.split(r"[;；\n|]", str(value))
        # Do not split a comma inside a legal person's name or infer an IPC suffix.
        return sorted({str(v).strip() for v in values if str(v).strip()}) or None
    if field == "claims":
        if isinstance(value, list):
            return deepcopy(value)
        return [{"no": None, "independent": None, "text": str(value).strip()}]
    return str(value).strip() or None


def _fingerprint(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _options(values):
    """Deduplicate values and attach every source without overwriting provenance."""
    grouped = {}
    for value, sources in values:
        if _empty(value):
            continue
        key = _fingerprint(value)
        item = grouped.setdefault(key, {"value": deepcopy(value), "sources": []})
        for source in sources:
            if source not in item["sources"]:
                item["sources"].append(deepcopy(source))
    return list(grouped.values())


def normalize_record(row: dict, source: dict) -> dict:
    """Return a JSON-compatible record, field provenance, conflicts and issues.

    ``source`` should identify the saved raw batch, row, and retrieval date.
    The caller retains the raw row.  Ambiguous identities use a reproducible hash
    of row AND source, so unrelated unidentified rows are not silently merged.
    """
    if not isinstance(row, dict) or not isinstance(source, dict):
        raise TypeError("row 和 source 必须为对象。")
    record = {"key": None, "authority": None, "number": None, "kind": None,
              "dates": {}, "legal": {}, "conflicts": {}, "issues": [], "provenance": []}
    observed = {}
    for field, aliases in ALIASES.items():
        candidates = []
        wanted = {_token(alias) for alias in aliases}
        for key, value in row.items():
            if _token(key) in wanted:
                candidates.append(value)
        nested = _get(row, field) if "." in field else None
        if nested is not None:
            candidates.append(nested)
        valid = []
        for value in candidates:
            if _empty(value):
                continue
            try:
                normalized = _normalize_value(field, value)
                if not _empty(normalized):
                    valid.append((normalized, [source]))
            except (TypeError, ValueError) as exc:
                record["issues"].append(f"invalid:{field}:{exc}")
        options = _options(valid)
        empty = [] if field in LIST_FIELDS else None
        if len(options) > 1:
            record["conflicts"][field] = options
            _put(record, field, empty)
        elif options:
            _put(record, field, options[0]["value"])
            observed[field] = options[0]["value"]
        else:
            _put(record, field, empty)
    publication = record.get("publication_number")
    if publication:
        record["authority"], record["number"], record["kind"] = parse_publication_number(publication)
        record["key"] = publication
    else:
        digest = hashlib.sha256(_fingerprint({"row": row, "source": source}).encode("utf-8")).hexdigest()[:24]
        record["key"] = "UNKNOWN-" + digest
        record["issues"].append("publication_identity_unknown")
    record["provenance"] = [{"source": deepcopy(source), "fields": observed}]
    for field in FIELDS:
        if _empty(_get(record, field)) and field not in record["conflicts"]:
            record["issues"].append(f"missing:{field}")
    record["issues"] = list(dict.fromkeys(record["issues"]))
    return record


def _observations(record, field):
    if field in record.get("conflicts", {}):
        return [(v["value"], v.get("sources", [])) for v in record["conflicts"][field]]
    value = _get(record, field)
    sources = [entry.get("source", {}) for entry in record.get("provenance", [])
               if entry.get("fields", {}).get(field) == value]
    return [(value, sources)]


def merge_records(existing: dict, incoming: dict) -> dict:
    """Merge only identical complete publication keys, preserving all conflicts.

    An identical UNKNOWN key may be reimported idempotently.  Different A/B kind
    codes are never combined even if their application or family ID agrees.
    """
    if not existing.get("key") or existing.get("key") != incoming.get("key"):
        raise ValueError("只能合并同一完整公开号；A/B 文本、不同或不明号码不得合并。")
    result = deepcopy(existing)
    result["conflicts"] = {}
    for field in FIELDS:
        options = _options(_observations(existing, field) + _observations(incoming, field))
        empty = [] if field in LIST_FIELDS else None
        if len(options) > 1:
            result["conflicts"][field] = options
            _put(result, field, empty)
        else:
            _put(result, field, options[0]["value"] if options else empty)
    for entry in incoming.get("provenance", []):
        if entry not in result["provenance"]:
            result["provenance"].append(deepcopy(entry))
    result["issues"] = list(dict.fromkeys(existing.get("issues", []) + incoming.get("issues", [])))
    result["issues"] = [issue for issue in result["issues"]
                        if not (issue.startswith("missing:") and not _empty(_get(result, issue[8:])))]
    for field in result["conflicts"]:
        marker = f"conflict:{field}"
        if marker not in result["issues"]:
            result["issues"].append(marker)
    return result
