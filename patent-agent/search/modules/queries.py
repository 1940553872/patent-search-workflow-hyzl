"""Check the supported Patsnap Boolean subset; never execute a website query.

This validates local structure, not the platform's availability or hit count.
Fields and operators follow the official pages recorded in source_capabilities.
"""
from __future__ import annotations
import re

from shared.contracts import ContractError

FIELDS = {"TACD_ALL", "TACD", "TAC_ALL", "TAC", "TA_ALL", "TA", "TTL_ALL", "TTL",
          "ABST_ALL", "ABST", "CLMS_ALL", "CLMS", "DESC_ALL", "DESC", "PN", "AUTHORITY", "IPC", "CPC"}
TEXT_FIELDS = FIELDS - {"AUTHORITY", "IPC", "CPC"}
EXPORT_FIELDS = "公开号、申请号、标题、申请人、公开日、申请日、优先权日、文献类型、法律状态、状态日期、IPC、摘要、详情链接、同族标识"


def validate_expression(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 10000:
        raise ContractError("检索式必须为非空文本，且不超过 10000 字符")
    if any(ord(c) < 32 for c in expression):
        raise ContractError("检索式应为一行；操作说明放入 purpose，不放入检索式")
    tokens = re.findall(r'"[^"\\]*"|[():]|[^\s():"]+', expression)
    # Tokenization must account for every non-whitespace character.
    if re.sub(r"\s", "", "".join(tokens)) != re.sub(r"\s", "", expression):
        raise ContractError("检索式引号未闭合或包含不支持的转义")
    position = 0
    fields = set()

    def take():
        nonlocal position
        if position >= len(tokens):
            raise ContractError("检索式缺少操作数或右括号")
        token = tokens[position]
        position += 1
        return token

    def peek():
        return tokens[position] if position < len(tokens) else None

    def primary(depth=0, within_field=False):
        if depth > 32:
            raise ContractError("检索式括号层数超过 32")
        token = take()
        if token == "NOT":
            primary(depth + 1, within_field)
        elif token == "(":
            expression_part(depth + 1, within_field)
            if take() != ")":
                raise ContractError("检索式括号不匹配")
        elif token in {"AND", "OR", ")", ":"} or token == '""':
            raise ContractError("检索式存在空条件或多余操作符")
        elif peek() == ":":
            if within_field or token not in FIELDS:
                raise ContractError(f"未支持或嵌套的检索字段：{token}")
            take()
            fields.add(token)
            primary(depth + 1, True)
        elif token in {"and", "or", "not"}:
            raise ContractError("布尔操作符使用大写 AND、OR、NOT")

    def conjunction(depth, within_field):
        primary(depth, within_field)
        while peek() == "AND":
            take()
            primary(depth, within_field)

    def expression_part(depth, within_field):
        conjunction(depth, within_field)
        while peek() == "OR":
            take()
            conjunction(depth, within_field)

    expression_part(0, False)
    if position != len(tokens):
        raise ContractError("检索式存在多余括号或缺少 AND/OR；英文短语须加英文双引号")
    if not fields.intersection(TEXT_FIELDS):
        raise ContractError("检索式必须明确文本字段或 PN 公开号字段")
    return sorted(fields)


def validate_query(query):
    if query.get("search_mode") != "expert":
        raise ContractError("新查询必须使用 expert 普通专家检索，不接受智慧芽 Agent 提示词")
    return validate_expression(query["query"])


def collection_instructions(run):
    return [
        "在智慧芽左侧选择专家搜索，粘贴完整检索式并点击搜索。",
        "高级搜索输入同式时，选择对应文本字段并输入字段括号内的关键词表达；受理局选择检索式指定范围。TACD_ALL 对应标题/摘要/权利要求/说明书及机器翻译数据。",
        "结果按公开文本展示，A/B 文本分别保留，不按同族只导出代表文献。",
        "新颖性采集不额外设置公开日或当前法律状态过滤；导入后按已确认基准日分支判断，日期缺失保持未知。" if run["spec"]["intent"] == "新颖性" else "按已确认规格执行；额外过滤条件必须一并记录，不能默默缩小范围。",
        "记录页面实际检索式、检索日期、结果总数和导出数量；改动检索内容时使用新查询编号。",
        "导出 CSV、XLSX 或 JSON。字段尽可能包含：" + EXPORT_FIELDS + "；无法取得的字段留空。",
        "全部导出登记 complete；还有未导出结果登记 truncated；成功且零结果登记 zero；查询失败登记 failed 并说明原因。",
    ]
