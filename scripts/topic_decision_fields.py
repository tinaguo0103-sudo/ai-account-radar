"""Canonical Feishu 04 分析与选题 field sets.

The table is a workflow experiment topic-card surface. Keep full debug output in
local CSV/MD files; only fields that the user reads or downstream steps consume
belong in Feishu.
"""
from __future__ import annotations


CARD_SUMMARY_FIELD = "卡片速读"

DAILY_WRITE_FIELDS = [
    "选题标题",
    CARD_SUMMARY_FIELD,
    "状态",
    "今日建议级别",
    "AI味风险",
    "推荐日期",
    "今日排名",
    "对应方向",
    "原始来源标题",
    "来源链接",
    "一句话Brief",
    "推荐理由",
    "不建议做的原因",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "可展示证据",
    "需要补的证据",
    "运行批次",
]

SCRIPT_MARK_FIELDS = [
    "是否已生成脚本稿",
]

FEEDBACK_FIELDS = [
    "选择原因标签",
    "人工一句话判断",
    "我的制作补充",
    "学习状态",
]

FEISHU_KEEP_FIELDS = DAILY_WRITE_FIELDS + SCRIPT_MARK_FIELDS + FEEDBACK_FIELDS

CORE_VISIBLE_FIELDS = [
    "选题标题",
    CARD_SUMMARY_FIELD,
    "状态",
    "选择原因标签",
    "今日建议级别",
    "AI味风险",
    "对应方向",
    "一句话Brief",
    "我要做的实验",
    "热点触发点",
    "我的工作流痛点",
    "旧流程痛点",
    "AI介入点",
    "验证方式",
    "可沉淀资产",
    "我的思考点",
    "可展示证据",
    "需要补的证据",
    "推荐理由",
    "不建议做的原因",
]

DETAIL_VISIBLE_FIELDS = CORE_VISIBLE_FIELDS + [
    "原始来源标题",
    "来源链接",
    "推荐日期",
    "今日排名",
    "运行批次",
    "人工一句话判断",
    "我的制作补充",
    "学习状态",
    *SCRIPT_MARK_FIELDS,
]

SELECTION_REASON_OPTIONS = [
    "有真实业务现场",
    "实验能马上做",
    "证据够",
    "资产价值高",
    "判断够强",
    "太泛",
    "太像资讯",
    "没有我的经验",
    "素材不够",
    "制作成本高",
    "事实风险高",
    "以后再说",
]

FIELD_OPTIONS = {
    "状态": ["待判断", "进入Brief", "本周做", "暂存", "归档", "不做"],
    "今日建议级别": ["今日最值得做", "可选候选", "暂存观察", "不建议制作"],
    "AI味风险": ["低", "中", "高"],
    "对应方向": ["真实工作流改造", "AI导演工作流", "汽车与内容营销", "AI业务定调", "AI项目复盘"],
    "学习状态": ["待学习", "待确认学习", "已学习", "忽略"],
}

MULTI_SELECT_FIELD_OPTIONS = {
    "选择原因标签": SELECTION_REASON_OPTIONS,
}


def field_create_body(field_name: str) -> dict:
    if field_name in FIELD_OPTIONS:
        return {
            "field_name": field_name,
            "type": 3,
            "property": {
                "options": [
                    {"name": option, "color": index % 10}
                    for index, option in enumerate(FIELD_OPTIONS[field_name])
                ],
            },
        }
    if field_name in MULTI_SELECT_FIELD_OPTIONS:
        return {
            "field_name": field_name,
            "type": 4,
            "property": {
                "options": [
                    {"name": option, "color": index % 10}
                    for index, option in enumerate(MULTI_SELECT_FIELD_OPTIONS[field_name])
                ],
            },
        }
    return {"field_name": field_name, "type": 1}


def normalize_card_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "").strip()
        return ""
    return str(value).strip()


def compact_card_value(value: object, limit: int = 52) -> str:
    text = " ".join(normalize_card_value(value).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def card_summary_from_fields(fields: dict) -> str:
    """Build the single card body the user can read without opening the record."""
    status = compact_card_value(fields.get("状态"), 12) or "待判断"
    level = compact_card_value(fields.get("今日建议级别"), 16) or "未评级"
    risk = compact_card_value(fields.get("AI味风险"), 8) or "未标"
    direction = compact_card_value(fields.get("对应方向"), 18) or "未标方向"
    lines = [
        f"状态：{status}｜建议：{level}｜AI味：{risk}｜方向：{direction}",
    ]
    summary_specs = [
        ("Brief", "一句话Brief", 76),
        ("实验", "我要做的实验", 76),
        ("痛点", "我的工作流痛点", 64),
        ("验证", "验证方式", 68),
        ("证据", "可展示证据", 62),
        ("缺口", "需要补的证据", 62),
        ("资产", "可沉淀资产", 52),
    ]
    for label, field_name, limit in summary_specs:
        value = compact_card_value(fields.get(field_name), limit)
        if value:
            lines.append(f"{label}：{value}")
    lines.append("操作：改状态；点选择原因标签；必要时写人工一句话判断。")
    return "\n".join(lines)
