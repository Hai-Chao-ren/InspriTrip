from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from inspitrip.paths import DEMO_DATA_DIR


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_ENTITIES = DEMO_DATA_DIR / "entities.jsonl"


def _expected(
    *,
    scope: str = "in_domain",
    task_type: str = "destination_discovery",
    target_destination: str | None = None,
    hard: dict[str, Any] | None = None,
    exclusions: list[str] | None = None,
    mood: list[str] | None = None,
    vibe: list[str] | None = None,
    activity: list[str] | None = None,
    aspects: list[str] | None = None,
    semantic_must_include: list[str] | None = None,
    semantic_must_not_include: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scope": scope,
        "task_type": task_type,
        "target_destination": target_destination,
    }
    if hard is not None:
        result["hard_constraints"] = hard
    if exclusions is not None:
        result["exclusions"] = exclusions
    soft: dict[str, list[str]] = {}
    if mood is not None:
        soft["mood"] = mood
    if vibe is not None:
        soft["vibe"] = vibe
    if activity is not None:
        soft["activity"] = activity
    if soft:
        result["soft_preferences"] = soft
    if aspects is not None:
        result["evidence_aspects"] = aspects
    if semantic_must_include is not None:
        result["semantic_must_include"] = semantic_must_include
    if semantic_must_not_include is not None:
        result["semantic_must_not_include"] = semantic_must_not_include
    return result


def _single(
    case_id: str,
    bucket: str,
    query: str,
    expected: dict[str, Any],
    *,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "id": case_id,
        "bucket": bucket,
        "turns": [{"query": query}],
        "expected": expected,
        "evaluation_targets": ["query_plan"],
        "retrieval_adjudication": "pending_human" if expected.get("scope") == "in_domain" else "not_applicable",
        "notes": notes,
    }


def _feeling_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    mood_specs = [
        ("想找个地方一个人安静待着", "mood_unwind", "安静"),
        ("周末只想发呆，别安排得太满", "mood_unwind", "发呆"),
        ("想躲开人群独处两天", "mood_unwind", "独处"),
        ("最近很累，想出去回血", "mood_heal", "回血"),
        ("想找个松弛治愈的地方", "mood_heal", "治愈"),
        ("周末想彻底放松一下", "mood_heal", "放松"),
        ("想和对象过个浪漫周末", "mood_romantic", "浪漫"),
        ("想找适合约会、有氛围感的地方", "mood_romantic", "约会"),
        ("想来点刺激和冒险", "mood_excited", "冒险"),
        ("周末想挑战一下自己", "mood_excited", "挑战"),
        ("想看看有年代感的旧时光", "mood_nostalgic", "年代感"),
        ("想去一个很复古、让人怀旧的地方", "mood_nostalgic", "怀旧"),
        ("想和朋友热热闹闹玩两天", "mood_social", "热闹"),
        ("一群人周末欢聚去哪里", "mood_social", "欢聚"),
        ("最近没灵感，想去文艺一点的地方充电", "mood_inspired", "灵感"),
        ("想找个有审美、能激发灵感的周末去处", "mood_inspired", "审美"),
        ("不想做攻略，想随性走走", "mood_freedom", "随性"),
        ("想来一次说走就走的短途", "mood_freedom", "说走就走"),
        ("想安静独处，也想让自己放松下来", "mood_unwind", "独处"),
        ("想治愈一点，但不要把行程排满", "mood_heal", "治愈"),
    ]
    for index, (query, tag, token) in enumerate(mood_specs, 1):
        moods = [tag, "mood_heal"] if index == 19 else [tag]
        rows.append(
            _single(
                f"QG-FEEL-MOOD-{index:03d}",
                "feeling_mood",
                query,
                _expected(mood=moods, semantic_must_include=[token]),
            )
        )

    vibe_specs = [
        ("想找小众冷门、人少一点的地方", "vibe_niche", "小众"),
        ("避开热门景点，想去冷门去处", "vibe_niche", "冷门"),
        ("想要原生态、没怎么开发的感觉", "vibe_unspoiled", "原生态"),
        ("喜欢质朴一点、不精修的地方", "vibe_unspoiled", "质朴"),
        ("想去有设计感、有调性的地方", "vibe_artsy", "设计感"),
        ("周末想逛文艺小资的街区", "vibe_artsy", "文艺"),
        ("想亲近自然，最好有山野感", "vibe_nature", "自然"),
        ("想找森林湖边那种自然环境", "vibe_nature", "森林"),
        ("想体验本地生活和烟火气", "vibe_local", "烟火气"),
        ("想钻进老街小巷看看市井生活", "vibe_local", "市井"),
        ("想住得舒服一点，整体精致惬意", "vibe_cozy", "惬意"),
        ("想要有品质感但不赶行程", "vibe_cozy", "品质感"),
        ("想去古朴的古村走走", "vibe_ancient", "古朴"),
        ("喜欢有历史感的古镇", "vibe_ancient", "历史感"),
        ("想感受都市潮流和时髦商圈", "vibe_urban", "潮流"),
        ("周末想逛有活力的城市街区", "vibe_urban", "城市"),
        ("小众、自然、不要太精致的地方", "vibe_niche", "小众"),
        ("想要古朴又有本地烟火气", "vibe_ancient", "古朴"),
        ("想去自然野一点的地方", "vibe_nature", "自然"),
        ("喜欢文艺但不要千篇一律", "vibe_artsy", "文艺"),
    ]
    for index, (query, tag, token) in enumerate(vibe_specs, 1):
        vibes = [tag]
        if index == 17:
            vibes.append("vibe_nature")
        elif index == 18:
            vibes.append("vibe_local")
        rows.append(
            _single(
                f"QG-FEEL-VIBE-{index:03d}",
                "feeling_vibe",
                query,
                _expected(vibe=vibes, semantic_must_include=[token]),
            )
        )

    soft_activity_specs = [
        ("想安静待着，顺便看看海", "act_sea"),
        ("主要想放松，可以走一小段徒步", "act_hike"),
        ("想安静待着，顺便露营也行", "act_camp"),
        ("想随便逛逛，可以去古镇", "act_town"),
        ("想发呆，顺便喝杯咖啡", "act_cafe"),
        ("主要想找灵感，可以看看展", "act_art"),
        ("想吹风放松，顺便骑骑车", "act_ride"),
        ("想慢下来，可以住一晚民宿", "act_stay"),
        ("主要想休息，顺便泡个温泉", "act_hotspring"),
        ("想体验烟火气，可以吃点当地小吃", "act_food"),
        ("想独处，天气好就顺便赶海", "act_sea"),
        ("想亲近自然，可以安排轻徒步", "act_hike"),
        ("想看自然风景，露营不是必须", "act_camp"),
        ("想找古朴氛围，老街可以顺便逛", "act_town"),
        ("想去文艺点的地方，咖啡店有就更好", "act_cafe"),
        ("想找灵感，美术馆不是硬要求", "act_art"),
        ("想放松，能骑行更好", "act_ride"),
        ("想放松，住特色民宿加分", "act_stay"),
        ("想治愈一下，有温泉更好", "act_hotspring"),
        ("想逛本地街巷，顺便觅食", "act_food"),
    ]
    for index, (query, tag) in enumerate(soft_activity_specs, 1):
        rows.append(
            _single(
                f"QG-FEEL-SOFTACT-{index:03d}",
                "feeling_soft_activity",
                query,
                _expected(activity=[tag]),
            )
        )

    hard_activity_specs = [
        ("这次必须看海，其他都随意", "act_sea"),
        ("一定要能赶海", "act_sea"),
        ("必须有徒步路线", "act_hike"),
        ("这趟就是要爬山", "act_hike"),
        ("一定要能露营看星空", "act_camp"),
        ("必须安排露营", "act_camp"),
        ("一定要逛古镇或古村", "act_town"),
        ("这次主要就是逛老街", "act_town"),
        ("必须有值得逛的咖啡店", "act_cafe"),
        ("这次就是去喝咖啡逛书店", "act_cafe"),
        ("必须能看展或逛美术馆", "act_art"),
        ("这趟一定要去博物馆", "act_art"),
        ("必须能骑行", "act_ride"),
        ("想专门安排一次环湖骑行", "act_ride"),
        ("必须住一晚特色民宿", "act_stay"),
        ("这趟要以住民宿为主", "act_stay"),
        ("一定要能泡温泉", "act_hotspring"),
        ("这次必须安排泡汤", "act_hotspring"),
        ("必须能吃到当地特色美食", "act_food"),
        ("这趟就是去探店觅食", "act_food"),
    ]
    for index, (query, tag) in enumerate(hard_activity_specs, 1):
        rows.append(
            _single(
                f"QG-FEEL-HARDACT-{index:03d}",
                "feeling_hard_activity",
                query,
                _expected(hard={"must_have_activities": [tag]}),
            )
        )

    combined_specs = [
        ("一个人安静看海，想彻底放空", ["mood_unwind"], ["vibe_nature"], "act_sea"),
        ("情侣想去浪漫又自然的海边看日落", ["mood_romantic"], ["vibe_nature"], "act_sea"),
        ("朋友一起热闹点，主要去找美食", ["mood_social"], [], "act_food"),
        ("想去小众自然的地方轻徒步", [], ["vibe_niche", "vibe_nature"], "act_hike"),
        ("想找古朴安静的古村慢慢逛", ["mood_unwind"], ["vibe_ancient"], "act_town"),
        ("想去文艺有设计感的地方看展", ["mood_inspired"], ["vibe_artsy"], "act_art"),
        ("想随性去海边骑行，不做太多攻略", ["mood_freedom"], ["vibe_nature"], "act_ride"),
        ("最近很累，想住舒服的民宿回血", ["mood_heal"], ["vibe_cozy"], "act_stay"),
        ("想来点冒险，必须有山野徒步", ["mood_excited"], ["vibe_nature"], "act_hike"),
        ("想怀旧，去有历史感和烟火气的地方吃小吃", ["mood_nostalgic"], ["vibe_ancient", "vibe_local"], "act_food"),
        ("想避开人潮，一个人去原生态海边看海", ["mood_unwind"], ["vibe_niche", "vibe_nature"], "act_sea"),
        ("想浪漫约会，顺便喝咖啡", ["mood_romantic"], [], "act_cafe"),
        ("朋友聚会想逛潮流街区顺便探店", ["mood_social"], ["vibe_urban"], "act_food"),
        ("想治愈放松，最好能泡温泉", ["mood_heal"], [], "act_hotspring"),
        ("想自由一点，在自然里露营看星空", ["mood_freedom"], ["vibe_nature"], "act_camp"),
        ("想找审美在线又不喧闹的地方", ["mood_inspired", "mood_unwind"], ["vibe_artsy"], None),
        ("想去市井、有烟火气的地方随便走走", ["mood_freedom"], ["vibe_local"], None),
        ("想挑战自己，但也希望景色自然", ["mood_excited"], ["vibe_nature"], None),
        ("想和对象住得精致舒服一点", ["mood_romantic"], ["vibe_cozy"], "act_stay"),
        ("想找冷门古村，一个人安静逛", ["mood_unwind"], ["vibe_niche", "vibe_ancient"], "act_town"),
    ]
    for index, (query, moods, vibes, activity) in enumerate(combined_specs, 1):
        weak_activity = bool(
            activity
            and any(marker in query for marker in ("顺便", "更好", "最好", "加分", "不是必须", "也行", "可以"))
        )
        hard_activities = [activity] if activity and not weak_activity else []
        if index == 7:
            hard_activities.insert(0, "act_sea")
        hard = {"must_have_activities": hard_activities} if hard_activities else None
        soft_activity = [activity] if activity and weak_activity else None
        rows.append(
            _single(
                f"QG-FEEL-COMBO-{index:03d}",
                "feeling_combined",
                query,
                _expected(
                    hard=hard,
                    mood=moods,
                    vibe=vibes,
                    activity=soft_activity,
                ),
            )
        )
    assert len(rows) == 100
    return rows


def _negation_cases() -> list[dict[str, Any]]:
    specs: list[tuple[str, dict[str, Any], str]] = [
        ("不要爬山，只想看海", _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=["act_hike"]), "activity_negation"),
        ("不想徒步，咖啡店可以有", _expected(exclusions=["act_hike"], activity=["act_cafe"]), "activity_negation"),
        ("别安排露营，必须住民宿", _expected(hard={"must_have_activities": ["act_stay"]}, exclusions=["act_camp"]), "activity_negation"),
        ("拒绝骑行，主要逛古镇", _expected(hard={"must_have_activities": ["act_town"]}, exclusions=["act_ride"]), "activity_negation"),
        ("不泡温泉，只想吃当地美食", _expected(hard={"must_have_activities": ["act_food"]}, exclusions=["act_hotspring"]), "activity_negation"),
        ("不要看展，想去海边发呆", _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=["act_art"], mood=["mood_unwind"]), "activity_negation"),
        ("不想逛古镇，必须能徒步", _expected(hard={"must_have_activities": ["act_hike"]}, exclusions=["act_town"]), "activity_negation"),
        ("别找咖啡店，想去自然里露营", _expected(hard={"must_have_activities": ["act_camp"]}, exclusions=["act_cafe"]), "activity_negation"),
        ("不住民宿，当天来回看海", _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=["act_stay"]), "activity_negation"),
        ("不要安排美食探店，主要任务是骑行", _expected(hard={"must_have_activities": ["act_ride"]}, exclusions=["act_food"]), "activity_negation"),
        ("不要网红景点，想安静点", _expected(exclusions=["网红"], mood=["mood_unwind"], aspects=["crowd"]), "non_activity_negation"),
        ("避开人多拥挤的地方", _expected(exclusions=["拥挤"], aspects=["crowd"]), "non_activity_negation"),
        ("不想去商业化严重的地方", _expected(exclusions=["商业化"], aspects=["commercialization"]), "non_activity_negation"),
        ("拒绝网红和人挤人", _expected(exclusions=["网红", "拥挤"], aspects=["crowd"]), "non_activity_negation"),
        ("不要太商业化，也不要热门打卡点", _expected(exclusions=["商业化", "网红"], aspects=["commercialization", "crowd"]), "non_activity_negation"),
        ("想找没那么商业化的古村", _expected(hard={"must_have_activities": ["act_town"]}, exclusions=["商业化"], aspects=["commercialization"]), "non_activity_negation"),
        ("人多的地方就算了，想独处", _expected(exclusions=["拥挤"], mood=["mood_unwind"], aspects=["crowd"]), "non_activity_negation"),
        ("网红不网红无所谓，主要想看海", _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=[]), "negation_cancellation"),
        ("商业化不是硬伤，只要交通方便", _expected(exclusions=[], aspects=["transport"]), "negation_cancellation"),
        ("不要求人少，热闹一点也行", _expected(exclusions=[], mood=["mood_social"]), "negation_cancellation"),
        ("不想错过看海", _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=[]), "double_negation"),
        ("不能没有当地美食", _expected(hard={"must_have_activities": ["act_food"]}, exclusions=[]), "double_negation"),
        ("不是不想徒步，只是别太累", _expected(exclusions=[], activity=["act_hike"]), "double_negation"),
        ("不是不能坐轮渡，只是不想折腾太久", _expected(hard={"transport_modes": []}, exclusions=[]), "double_negation"),
        ("没有说不要咖啡店", _expected(exclusions=[]), "reported_negation"),
        ("朋友说别爬山，但我本人想徒步", _expected(hard={"must_have_activities": ["act_hike"]}, exclusions=[]), "reported_negation"),
        ("上次不想看海，这次必须看海", _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=[]), "correction"),
        ("我不是要避开热闹，反而想人多一点", _expected(exclusions=[], mood=["mood_social"]), "correction"),
        ("不要徒步和露营，只想逛老街", _expected(hard={"must_have_activities": ["act_town"]}, exclusions=["act_hike", "act_camp"]), "multi_negation"),
        ("别去网红打卡点，也别安排爬山", _expected(exclusions=["网红", "act_hike"], aspects=["crowd"]), "multi_negation"),
    ]
    return [
        _single(f"QG-NEG-{index:03d}", bucket, query, expected)
        for index, (query, expected, bucket) in enumerate(specs, 1)
    ]


def _numeric_transport_cases() -> list[dict[str, Any]]:
    specs: list[tuple[str, dict[str, Any], str]] = [
        ("上海出发，预算800以内", _expected(hard={"origin": "上海", "budget_max": 800}), "budget_origin"),
        ("杭州出发，人均一千元以下", _expected(hard={"origin": "杭州", "budget_max": 1000}), "chinese_number"),
        ("苏州出发，两个人总预算两千", _expected(hard={"origin": "苏州", "budget_max": 2000}), "chinese_number"),
        ("预算一千五百块以内", _expected(hard={"budget_max": 1500}), "chinese_number"),
        ("人均不要超过六百五十元", _expected(hard={"budget_max": 650}), "chinese_number"),
        ("预算1200到1800，最多1800", _expected(hard={"budget_max": 1800}), "number_range"),
        ("最多花2k", _expected(hard={"budget_max": 2000}), "number_unit"),
        ("人均三百左右，不是硬上限", _expected(hard={"budget_max": None}), "soft_number"),
        ("预算不限，想轻松一点", _expected(hard={"budget_max": None}), "clear_number"),
        ("不考虑预算，先看感觉", _expected(hard={"budget_max": None}), "clear_number"),
        ("周末两天一夜", _expected(hard={"days_max": 2}), "days"),
        ("最多玩三天", _expected(hard={"days_max": 3}), "chinese_number"),
        ("一天来回", _expected(hard={"days_max": 1}), "chinese_number"),
        ("四天以内都可以", _expected(hard={"days_max": 4}), "chinese_number"),
        ("时间不限，想慢慢玩", _expected(hard={"days_max": None}), "clear_number"),
        ("上海出发三小时内", _expected(hard={"origin": "上海", "travel_time_max": 180}), "travel_time"),
        ("杭州出发最多两个半小时", _expected(hard={"origin": "杭州", "travel_time_max": 150}), "chinese_decimal"),
        ("苏州出发90分钟以内", _expected(hard={"origin": "苏州", "travel_time_max": 90}), "travel_time"),
        ("路上别超过四小时", _expected(hard={"travel_time_max": 240}), "chinese_number"),
        ("交通时间不限", _expected(hard={"travel_time_max": None}), "clear_number"),
        ("上海出发，必须坐高铁", _expected(hard={"origin": "上海", "transport_modes": ["高铁"]}), "transport_mode"),
        ("杭州出发只接受自驾", _expected(hard={"origin": "杭州", "transport_modes": ["自驾"]}), "transport_mode"),
        ("苏州出发，公共交通都可以", _expected(hard={"origin": "苏州", "transport_modes": ["公共交通"]}), "transport_mode"),
        ("可以高铁也可以大巴", _expected(hard={"transport_modes": ["高铁", "大巴"]}), "transport_mode"),
        ("这趟必须坐轮渡", _expected(hard={"transport_modes": ["轮渡"]}), "transport_mode"),
        ("不要自驾，优先公共交通", _expected(exclusions=["自驾"]), "transport_negation"),
        ("地铁能到最好，但不是必须", _expected(hard={"transport_modes": []}), "soft_transport"),
        ("上海出发，三小时内，高铁优先", _expected(hard={"origin": "上海", "travel_time_max": 180, "transport_modes": []}), "soft_transport"),
        ("杭州出发两天，预算一千五，必须自驾", _expected(hard={"origin": "杭州", "days_max": 2, "budget_max": 1500, "transport_modes": ["自驾"]}), "compound_constraint"),
        ("苏州出发，最多三天两千元，公共交通四小时内", _expected(hard={"origin": "苏州", "days_max": 3, "budget_max": 2000, "travel_time_max": 240, "transport_modes": ["公共交通"]}), "compound_constraint"),
    ]
    return [
        _single(f"QG-NUM-{index:03d}", bucket, query, expected)
        for index, (query, expected, bucket) in enumerate(specs, 1)
    ]


def _multi_turn_cases() -> list[dict[str, Any]]:
    specs = [
        (["上海出发想安静看海", "预算补充到1500以内"], _expected(hard={"origin": "上海", "budget_max": 1500, "must_have_activities": ["act_sea"]}, mood=["mood_unwind"]), ["hard_constraints.budget_max"], []),
        (["杭州出发预算1000", "预算改成1500"], _expected(hard={"origin": "杭州", "budget_max": 1500}), ["hard_constraints.budget_max"], []),
        (["苏州出发预算800", "预算不限了"], _expected(hard={"origin": "苏州", "budget_max": None}), [], ["hard_constraints.budget_max"]),
        (["想安静一点，可以徒步", "再安静一点，但不要徒步"], _expected(exclusions=["act_hike"], mood=["mood_unwind"]), [], []),
        (["上海出发两天", "改成杭州出发"], _expected(hard={"origin": "杭州", "days_max": 2}), ["hard_constraints.origin"], []),
        (["想看海，最多三天", "一天来回吧"], _expected(hard={"days_max": 1, "must_have_activities": ["act_sea"]}), ["hard_constraints.days_max"], []),
        (["必须高铁，三小时内", "交通方式不限"], _expected(hard={"travel_time_max": 180, "transport_modes": []}), [], ["hard_constraints.transport_modes"]),
        (["想去小众古镇", "古镇不是必须，安静就行"], _expected(mood=["mood_unwind"], vibe=["vibe_niche"]), [], ["hard_constraints.must_have_activities"]),
        (["预算2000，想住民宿", "不住民宿了，改成当天回"], _expected(hard={"budget_max": 2000, "days_max": 1}, exclusions=["act_stay"]), ["hard_constraints.days_max"], []),
        (["想热闹一点", "还是想独处，别太多人"], _expected(exclusions=["拥挤"], mood=["mood_unwind"]), ["soft_preferences.mood"], []),
        (["上海出发", "三小时内"], _expected(hard={"origin": "上海", "travel_time_max": 180}), ["hard_constraints.travel_time_max"], []),
        (["三小时内", "我从苏州出发"], _expected(hard={"origin": "苏州", "travel_time_max": 180}), ["hard_constraints.origin"], []),
        (["想去不商业化的地方", "商业化无所谓了"], _expected(exclusions=[]), [], ["exclusions"]),
        (["必须看海", "再加一个必须骑行"], _expected(hard={"must_have_activities": ["act_sea", "act_ride"]}), ["hard_constraints.must_have_activities"], []),
        (["必须看海和徒步", "徒步去掉"], _expected(hard={"must_have_activities": ["act_sea"]}, exclusions=["act_hike"]), ["hard_constraints.must_have_activities"], []),
        (["杭州出发两天预算1200", "预算加到1800，天数不变"], _expected(hard={"origin": "杭州", "days_max": 2, "budget_max": 1800}), ["hard_constraints.budget_max"], []),
        (["想文艺看展", "看展不是必须，咖啡店加分"], _expected(mood=["mood_inspired"], activity=["act_cafe"]), ["soft_preferences.activity"], ["hard_constraints.must_have_activities"]),
        (["想露营看星空", "如果天气不好就不露营"], _expected(activity=["act_camp"], aspects=["weather_season"]), ["soft_preferences.activity"], ["hard_constraints.must_have_activities"]),
        (["苏州出发只自驾", "改成公共交通也可以"], _expected(hard={"origin": "苏州", "transport_modes": ["公共交通"]}), ["hard_constraints.transport_modes"], []),
        (["想小众人少", "不用人少了，想和朋友热闹点"], _expected(exclusions=[], mood=["mood_social"]), ["soft_preferences.mood"], ["exclusions", "soft_preferences.vibe"]),
    ]
    rows = []
    for index, (turn_texts, expected, replace_slots, clear_slots) in enumerate(specs, 1):
        turns = []
        for turn_index, text in enumerate(turn_texts):
            turn: dict[str, Any] = {"query": text}
            if turn_index == len(turn_texts) - 1:
                turn["expected_state_actions"] = {
                    "replace_slots": replace_slots,
                    "clear_slots": clear_slots,
                }
            turns.append(turn)
        rows.append(
            {
                "id": f"QG-MULTI-{index:03d}",
                "bucket": "multi_turn_update",
                "turns": turns,
                "expected": expected,
                "evaluation_targets": ["query_state"],
                "retrieval_adjudication": "pending_human",
                "notes": "Evaluate the merged plan after the final turn.",
            }
        )
    return rows


def _load_active_destinations(path: Path) -> list[dict[str, str]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("entity_type") == "destination" and row.get("status") == "active":
                rows.append({"destination_id": row["entity_id"], "name": row["name"]})
    rows.sort(key=lambda item: (item["name"], item["destination_id"]))
    return rows


def _routing_cases(destinations: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not destinations:
        raise ValueError("at least one active destination is required")
    known = [destinations[index % len(destinations)] for index in range(8)]
    rows: list[dict[str, Any]] = []
    suffixes = ["怎么玩", "有什么值得体验", "两天怎么安排", "内部有哪些玩法", "适合怎么玩", "去那里做什么", "周末玩法", "有什么活动"]
    for index, (destination, suffix) in enumerate(zip(known, suffixes), 1):
        row = _single(
            f"QG-ROUTE-KNOWN-{index:03d}",
            "known_destination_experience",
            f"{destination['name']}{suffix}",
            _expected(task_type="experience_lookup", target_destination=destination["name"]),
        )
        row["retrieval_adjudication"] = "entity_snapshot_exact_name"
        row["relevant_destination_ids"] = [destination["destination_id"]]
        rows.append(row)

    routing_specs = [
        ("想去北京过周末", "out_of_region", "unsupported", "out_of_region"),
        ("推荐一个成都附近的古镇", "out_of_region", "unsupported", "out_of_region"),
        ("广州两天一夜去哪里", "out_of_region", "unsupported", "out_of_region"),
        ("想去国外海岛度假", "out_of_region", "unsupported", "out_of_region"),
        ("帮我订一家酒店", "not_supported_yet", "unsupported", "unsupported_service"),
        ("只想找一家海鲜餐厅", "not_supported_yet", "unsupported", "unsupported_service"),
        ("帮我买景区门票", "not_supported_yet", "unsupported", "unsupported_ticket"),
        ("查完整轮渡班次并替我订票", "not_supported_yet", "unsupported", "unsupported_ticket"),
        ("你好呀", "not_travel", "chitchat", "chitchat"),
        ("你是谁", "not_travel", "chitchat", "chitchat"),
        ("讲个笑话", "not_travel", "chitchat", "chitchat"),
        ("今天心情怎么样", "not_travel", "chitchat", "chitchat"),
    ]
    for index, (query, scope, task_type, bucket) in enumerate(routing_specs, 1):
        rows.append(
            _single(
                f"QG-ROUTE-{index:03d}",
                bucket,
                query,
                _expected(scope=scope, task_type=task_type),
            )
        )
    assert len(rows) == 20
    return rows


def build_query_gold(destinations: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = (
        _feeling_cases()
        + _negation_cases()
        + _numeric_transport_cases()
        + _multi_turn_cases()
        + _routing_cases(destinations)
    )
    assert len(rows) == 200
    assert len({row["id"] for row in rows}) == len(rows)
    return rows


def build_retrieval_gold(destinations: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    query_templates = ["{name}", "想去{name}", "{name}怎么玩"]
    for destination in destinations:
        for variant, template in enumerate(query_templates, 1):
            rows.append(
                {
                    "id": f"RG-{destination['destination_id']}-{variant}",
                    "bucket": "exact_destination_name" if variant == 1 else "known_destination_intent",
                    "query": template.format(name=destination["name"]),
                    "relevant_destination_ids": [destination["destination_id"]],
                    "adjudication": "entity_snapshot_exact_name",
                }
            )
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic query/retrieval gold snapshots.")
    parser.add_argument("--entities", type=Path, default=DEFAULT_ENTITIES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_EVAL_DIR)
    args = parser.parse_args()
    destinations = _load_active_destinations(args.entities)
    if not destinations:
        raise SystemExit("expected at least one active destination")
    query_rows = build_query_gold(destinations)
    retrieval_rows = build_retrieval_gold(destinations)
    _write_jsonl(args.output_dir / "query_gold.jsonl", query_rows)
    _write_jsonl(args.output_dir / "retrieval_gold.jsonl", retrieval_rows)
    print(json.dumps({"query_gold": len(query_rows), "retrieval_gold": len(retrieval_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
