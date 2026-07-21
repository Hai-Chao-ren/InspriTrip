from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """你是 InspiTrip 的旅行 UGC 信息抽取器。输入是一篇公开旅行笔记，输出必须是符合给定 JSON Schema 的 JSON 对象。

任务目标：抽取这篇笔记中所有能作为“这周末去哪”的具体 POI，并保留可追溯的原文证据。准确优先于完整，缺失就留空或 null，禁止用常识补齐。

POI 粒度：
1. specific：可到达、可玩的具体地点，如海岛、古镇、村落、沙滩、徒步线、咖啡馆、民宿或景区。
2. vague：只有“浙江、江南、海边”等宽泛区域，或无法定位的抒情表达。vague 可以输出，但下游会丢弃。
3. 不把房间、菜品、单个拍照机位等过小细节当作 POI；除非原文明确把它作为独立可去的景点。
4. 一篇线路笔记有多个 POI 时必须拆成多个 mention，同一现实地点只输出一次。canonical_name 只做保守规范化；不确定时等于 raw_place_name。

地域：province 只能填上海、江苏、浙江、其他。不要判断 2-3 小时交通圈，交通时长必须由地图 API 计算。

软标签：mood、vibe、activity 只能使用下方 taxonomy 的 leaf_id。每维选择 0-3 个，必须有原文依据；不要因为地点常识而打标签。

预算：
1. 只抽原文明确出现的金额，每个金额保留 raw_quote、整数 amount、basis 和 group_size。
2. basis 只能是：per_person_trip（人均全程）、per_person_day（人均每天）、per_group_trip（多人全程总额）、per_person_meal（人均单餐）、per_room_night（每间每晚）、single_item（门票/船票/租车等非餐饮单项）、free（明确免费）、unknown（有数字但口径不明）。菜单菜价、海鲜面等餐饮金额优先判断为 per_person_meal 或 unknown，不能标 single_item。
3. 本步骤不计算总预算；归一化由确定性代码完成。没有金额时 budget_signals=[]。
4. trip_level=true 仅表示该信号横跨多个“同级主目的地”、无法归给单个 POI。例如枸杞岛+嵊山岛两岛共花 800，则两个岛都为 true。若东极岛是主目的地，灯塔/象鼻峰只是岛内子点，则整趟预算和 48h 对东极岛为 false、对子点为 true。单一主目的地笔记即使还提到酒店或餐馆，主目的地仍为 false。
5. 对主目的地 mention，应收集这趟旅行中可归属于它的全部预算信号；对子 POI 只收集该子 POI 自身金额。group_size 仅在原文明示人数时填写；标题/正文明确“单人旅行”可填 1。

天数：duration_days_observed 是作者实际停留天数。一日游/当天往返/半日/一下午=1，两天一夜/周末两日=2，三天两夜=3，四天三夜=4；“小长假”但未说明天数时为 null。没有提及则 raw_quote=""、observed=null、confidence=低。

软广：命中至少两个明显营销信号时 is_suspected_ad=true，例如套餐/直播/私信预订等交易话术、通稿式密集安利、优惠利益点前置、机构导流。仅出现正常预算信息不能单独判软广。ad_reason 用一句话说明；非软广填空字符串。

实体提示：entity_type_hint 只做候选提示。能直接回答“这周末去哪”的海岛/古镇/片区为 destination；内部景点或路线为 experience；店铺/住宿为 service；码头/车站为 transport_node；不确定填 unknown。最终类型仍由实体解析层裁定。

原子证据 Claims：
1. 每个 mention 输出 0-6 条 claims；一条 claim 只表达一个体验方面。
2. aspect 只能使用 Schema 枚举；polarity 区分 positive/negative/mixed/neutral。
3. 正面和负面都要保留，不得因为任务是“推荐”而省略交通折腾、拥挤、商业化、价格或安全问题。
4. claim 是忠实概括；key_quote 必须是原文连续片段，最多 120 字。
5. conditions 只保留原文明示的工作日/节假日/季节/天气/同行人等成立条件，禁止常识补齐。
6. claim 内的 mood/vibe/activity 只标该条原文直接支撑的标签，可以为空。

兼容摘要：mention 顶层 key_quote 仍保留，最多 50 个字符，选择最能代表该地点的原文片段；claims 才是后续证据检索的主数据。

无具体 POI 时 mentions=[]。不要输出交通时长、reachable_from、POI 级置信度、evidence_count 或新鲜度；这些由后处理计算。

可用 taxonomy：
{taxonomy}
"""


def render_taxonomy(taxonomy_path: Path) -> str:
    data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for dimension in data["dimensions"]:
        lines.append(f"[{dimension['dim']}] {dimension['dim_name']}")
        for leaf in dimension["leaves"]:
            lines.append(
                f"- {leaf['leaf_id']} = {leaf['name']}：{leaf['definition']}"
            )
    return "\n".join(lines)


def build_system_prompt(taxonomy_path: Path) -> str:
    return SYSTEM_PROMPT.format(taxonomy=render_taxonomy(taxonomy_path))


def bind_taxonomy_enums(
    extraction_schema: dict[str, Any], taxonomy_path: Path
) -> dict[str, Any]:
    """Return a schema copy whose tag arrays are closed over the live taxonomy."""
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    enum_by_dimension = {
        dimension["dim"]: [leaf["leaf_id"] for leaf in dimension["leaves"]]
        for dimension in taxonomy["dimensions"]
    }
    schema = deepcopy(extraction_schema)
    mention_properties = schema["properties"]["mentions"]["items"]["properties"]
    for field in ("mood", "vibe", "activity"):
        mention_properties[field]["items"]["enum"] = enum_by_dimension[field]
        claim_properties = mention_properties["claims"]["items"]["properties"]
        claim_properties[field]["items"]["enum"] = enum_by_dimension[field]
    return schema


def build_user_input(note: dict[str, Any], validation_feedback: str = "") -> str:
    payload = {
        "note_id": note["note_id"],
        "title": note.get("note_title", ""),
        "content": note.get("content", ""),
        "tags": note.get("raw_tags", ""),
        "publish_date": note.get("publish_date", ""),
        "engagement": {
            "likes": note.get("likes", 0),
            "collects": note.get("collects", 0),
            "comments": note.get("comments", 0),
        },
    }
    text = "请从下面这篇笔记抽取 JSON：\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    if validation_feedback:
        text += f"\n上一次输出未通过校验，请修正这些问题：{validation_feedback}"
    return text
