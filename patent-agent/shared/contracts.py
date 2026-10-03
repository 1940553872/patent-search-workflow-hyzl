"""Validate the deliberately small JSON Schema vocabulary used by this project.

Schemas are Draft 2020-12 documents. The offline validator implements exactly the
keywords below, and rejects any unsupported keyword instead of silently ignoring it.
No third-party validator or network resolution is needed.
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime
from pathlib import Path

SCHEMAS = Path(__file__).parent / "schemas"

class ContractError(ValueError):
    pass

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ContractError(f"无法读取 JSON：{path}；{exc}") from exc

def validate(name, value):
    schema = read_json(SCHEMAS / f"{name}.schema.json")
    _check(schema, value, schema, "$")
    return value

def _check(s, v, root, at):
    supported = {"$schema", "$id", "$defs", "$ref", "title", "description", "type",
                 "properties", "required", "additionalProperties", "items", "minItems",
                 "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "format",
                 "enum", "const", "minimum", "maximum", "anyOf"}
    if set(s) - supported:
        raise ContractError(f"Schema 含未实现的校验项：{set(s) - supported}")
    def fail(reason):
        raise ContractError(f"{at}：{reason}")
    if "$ref" in s:
        if not s["$ref"].startswith("#/"):
            fail("仅允许本文件内的 Schema 引用")
        ref = root
        for part in s["$ref"][2:].split("/"):
            ref = ref[part.replace("~1", "/").replace("~0", "~")]
        _check(ref, v, root, at)
    if "anyOf" in s:
        for branch in s["anyOf"]:
            try:
                _check(branch, v, root, at)
                break
            except ContractError:
                continue
        else:
            fail("不满足任何允许的数据结构")
    kinds = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list),
             "string": lambda x: isinstance(x, str), "boolean": lambda x: isinstance(x, bool),
             "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
             "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
             "null": lambda x: x is None}
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(kinds[t](v) for t in types):
            fail(f"应为 {types}")
    if "enum" in s and v not in s["enum"]:
        fail(f"值必须属于 {s['enum']}")
    if "const" in s and v != s["const"]:
        fail(f"值必须是 {s['const']}")
    if isinstance(v, dict):
        missing = set(s.get("required", [])) - v.keys()
        if missing:
            fail(f"缺少字段 {sorted(missing)}")
        props = s.get("properties", {})
        extras = set(v) - props.keys()
        if s.get("additionalProperties") is False and extras:
            fail(f"不允许的字段 {sorted(extras)}")
        for key, child in v.items():
            if key in props:
                _check(props[key], child, root, f"{at}.{key}")
            elif isinstance(s.get("additionalProperties"), dict):
                _check(s["additionalProperties"], child, root, f"{at}.{key}")
    elif isinstance(v, list):
        if len(v) < s.get("minItems", 0) or len(v) > s.get("maxItems", float("inf")):
            fail("数组长度不在允许范围内")
        if s.get("uniqueItems") and len({json.dumps(i, sort_keys=True) for i in v}) != len(v):
            fail("数组不允许重复项")
        for i, child in enumerate(v):
            if "items" in s:
                _check(s["items"], child, root, f"{at}[{i}]")
    elif isinstance(v, str):
        if len(v) < s.get("minLength", 0) or len(v) > s.get("maxLength", float("inf")):
            fail("文本长度不在允许范围内")
        if "pattern" in s and not re.search(s["pattern"], v):
            fail(f"文本格式不符合 {s['pattern']}")
        if "format" in s:
            try:
                if s["format"] == "date":
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                        raise ValueError()
                    date.fromisoformat(v)
                elif s["format"] == "date-time":
                    datetime.fromisoformat(v.replace("Z", "+00:00"))
                else:
                    fail("不支持的 format")
            except ValueError:
                fail("日期格式或日期值无效")
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        if v < s.get("minimum", -float("inf")) or v > s.get("maximum", float("inf")):
            fail("数值不在允许范围内")
