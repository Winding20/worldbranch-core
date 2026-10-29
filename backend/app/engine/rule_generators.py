"""按对象生成的选项（「每支无帅的军队」「每个拥兵的功臣」）。

有些决策天生是「对每一个符合条件的东西问一次」：
  - 某支军队没了主将 → 要选人接掌（候选来自人物表，按才略排序）
  - 某位将领兵多而忠低 → 赏还是削（赏格按兵力算）

这类选项列表没法用静态 JSON 写死，所以放在这里，由规则里的
``"options_from": "<名字>"`` 调用。生成器不认识任何具体地名或人名，
只认状态与规则传入的过滤条件。
"""

from __future__ import annotations

from typing import Any, Callable

from .decisions import candidates_for_command


def _army_of(entity: Any) -> Any:
    return entity


def appoint_commander(entity: Any, state: Any, env: dict[str, Any]) -> list[dict[str, Any]]:
    """给一支无帅的军队挑人：取才略最高的三位，外加「暂不遣帅」。"""
    army = _army_of(entity)
    player = env.get("player_faction", "tang")
    busy = {
        a.commander_key
        for a in state.armies.values()
        if a.commander_key and a.army_key
    }
    pool = [
        p
        for p in candidates_for_command(state.characters, player, limit=8)
        if p.character_key not in busy
    ][:3]

    options = [
        {
            "key": f"appoint_{p.character_key}",
            "label": f"以{p.name}为帅",
            "detail": (
                f"{p.office or '将领'}，才略 {p.ability:.2f}；"
                f"接掌{army.name}后士气回升"
            ),
            "effects": {
                "commander": {army.army_key: p.character_key},
                "morale": {army.army_key: 0.10},
            },
        }
        for p in pool
    ]
    options.append(
        {
            "key": "appoint_none",
            "label": "暂不遣帅，令副将权领",
            "detail": "不费钱粮，但无主帅之军战力大减，士气继续下滑",
            "effects": {"morale": {army.army_key: -0.08}},
        }
    )
    return options


def reward_general(entity: Any, state: Any, env: dict[str, Any]) -> list[dict[str, Any]]:
    """拥兵功臣：加官厚赏 / 削其兵权 / 不闻不问。

    赏格按所统兵力算；削权会把军队主帅清空并改授闲职——
    这些是「官制」层面的效果，由 resolve_decision 的 office / disarm 处理。
    """
    from .offices import SINECURE
    from .loyalty import DEFECT_LOYALTY, WARNING_LOYALTY

    person = entity
    own = [
        a for a in state.armies.values() if a.commander_key == person.character_key
    ]
    troops = sum(a.troops for a in own)
    cost = int(troops * 3)
    loyal = float(getattr(person, "loyalty", 0.5))
    if not (DEFECT_LOYALTY < loyal <= WARNING_LOYALTY) or troops <= 0:
        return []

    return [
        {
            "key": "reward",
            "label": "加官厚赏",
            "detail": (
                f"府库 −{cost:,} 贯，授同中书门下平章事，"
                "忠诚回升，然此例一开，诸将效尤"
            ),
            "effects": {
                "treasury": -cost,
                "loyalty": {person.character_key: 0.15},
                "office": {
                    person.character_key: {
                        "title": "同中书门下平章事",
                        "rank": 2,
                        "kind": "civil",
                    }
                },
                "legitimacy": -0.02,
            },
        },
        {
            "key": "strip_command",
            "label": "削其兵权",
            "detail": (
                f"夺其军，改授{SINECURE['title']}（闲职）；"
                "忠诚大跌，若其部曲不服则生变"
            ),
            "effects": {
                "loyalty": {person.character_key: -0.15},
                "disarm": [a.army_key for a in own],
                "office": {
                    person.character_key: {
                        "title": SINECURE["title"],
                        "rank": SINECURE["rank"],
                        "kind": SINECURE["kind"],
                    }
                },
                "legitimacy": 0.04,
            },
        },
        {
            "key": "ignore",
            "label": "不闻不问",
            "detail": "不费钱粮，但其离心日甚，恐终为患",
            "effects": {"loyalty": {person.character_key: -0.04}},
        },
    ]

#: 规则里用 "options_from": "<名字>" 引用
GENERATORS: dict[str, Callable[[Any, Any, dict[str, Any]], list[dict[str, Any]]]] = {
    "appoint_commander": appoint_commander,
    "reward_general": reward_general,
}


__all__ = ["GENERATORS", "appoint_commander", "reward_general"]