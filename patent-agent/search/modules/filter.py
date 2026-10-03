"""Hard-field and intent screening. This module never reads or interprets PDFs."""

from datetime import date, datetime, timedelta, timezone

from shared.rules import FAIL, PASS, UNKNOWN, conjunction, conflicted, evaluate, get_field, parse_date

CURRENT_STATES = frozenset(("有效", "授权有效", "审中", "在审", "实质审查", "实质审查中", "pending", "active", "granted"))
INACTIVE_STATES = frozenset(("失效", "无效", "终止", "届满", "驳回", "撤回", "撤销", "expired", "invalid", "withdrawn", "rejected", "lapsed"))


def _novelty(record, spec, today):
    base = parse_date(spec.get("base_date"))
    publication = parse_date(get_field(record, "dates.pub"))
    if base is None:
        return UNKNOWN, "unknown", ["新颖性基准日缺失或格式无效。"]
    if publication is None:
        return UNKNOWN, "unknown", ["公开日缺失、冲突或不是完整日期；不以申请日或优先权日代替。"]
    if publication < base:
        return PASS, "prior_art", ["公开日早于基准日，进入现有技术分支。"]
    authority = get_field(record, "authority")
    if not authority:
        return UNKNOWN, "unknown", ["公开日不早于基准日，公布机构尚待核实。"]
    if authority != "CN":
        return FAIL, "outside_date", ["公开日不早于基准日，且不是中国申请。"]
    application = parse_date(get_field(record, "dates.app"))
    if application is None:
        return UNKNOWN, "unknown", ["中国申请公开日不早于基准日，但申请日缺失或冲突。"]
    if application < base:
        return PASS, "conflicting_application", ["中国申请的申请日在前、公开日不早于基准日；仅为抵触申请候选，必须待核实。"]
    return FAIL, "outside_date", ["申请日与公开日均不早于基准日。"]


def _risk(record, today):
    authority = get_field(record, "authority")
    authority_value = UNKNOWN if not authority else PASS if authority == "CN" else FAIL
    state = get_field(record, "legal.state")
    as_of = parse_date(get_field(record, "legal.as_of"))
    current = parse_date(today)
    expiry_value = get_field(record, "legal.expiry")
    expiry = parse_date(expiry_value)
    reasons = []
    if as_of is None or current is None or as_of != current:
        legal_value = UNKNOWN
        reasons.append("法律状态未核实至本次评估日，不能据旧状态确认当前有效性。")
    elif isinstance(state, str) and state in CURRENT_STATES:
        if conflicted(record, "legal.expiry") or expiry_value and (expiry is None or expiry <= current):
            legal_value = UNKNOWN
            reasons.append("有效或审中状态与届满日期不一致，需核实。")
        else:
            legal_value = PASS
    elif isinstance(state, str) and state in INACTIVE_STATES:
        legal_value = FAIL
        reasons.append("本次评估日的法律状态明确不属于有效或审中。")
    else:
        legal_value = UNKNOWN
        reasons.append("法律状态缺失、不明确或来源冲突。")
    if authority_value == FAIL:
        reasons.append("侵权风险入口仅保留中国专利。")
    elif authority_value == UNKNOWN:
        reasons.append("公布机构缺失或冲突。")
    return conjunction((authority_value, legal_value)), "infringement_risk", reasons


def assess(record, spec, today=None):
    """Return value/branch/reasons/hard_value; soft preferences never exclude.

    Intent restrictions are ANDed with the complete user hard tree. Technical
    leaves stay UNKNOWN here, allowing a field OR branch to remain decisive.
    Legal-state freshness uses the assessment day; no undocumented day tolerance
    is invented. Callers can inject ``today`` for reproducible fixed snapshots.
    """
    today = today.isoformat() if isinstance(today, date) else today or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if spec.get("entry", "search") == "lookup":
        return {"value": PASS, "branch": None, "reasons": ["按号取文献，不筛选。"], "hard_value": PASS, "intent_value": PASS}
    hard_value = evaluate(spec.get("hard", {"op": "AND", "args": []}), record)
    intent = spec.get("intent")
    if intent == "新颖性":
        intent_value, branch, reasons = _novelty(record, spec, today)
    elif intent == "侵权风险":
        intent_value, branch, reasons = _risk(record, today)
    elif intent == "全景":
        intent_value, branch, reasons = PASS, "panorama", ["全景入口不附加日期截止条件。"]
    elif intent is None:
        intent_value, branch, reasons = PASS, "unspecified", []
    else:
        intent_value, branch, reasons = UNKNOWN, "unknown", ["未知检索意图。"]
    value = conjunction((hard_value, intent_value))
    if hard_value == FAIL:
        reasons.append("用户硬条件逻辑树为 FAIL。")
    elif hard_value == UNKNOWN:
        reasons.append("硬条件含待核实字段或尚未取证的技术特征。")
    return {"value": value, "branch": {"prior_art": "现有技术", "conflicting_application": "抵触申请"}.get(branch), "reasons": reasons,
            "hard_value": hard_value, "intent_value": intent_value}
