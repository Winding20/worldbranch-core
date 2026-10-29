"""从世界包的 decisions.json 生成决策（数据驱动的那条路）。

与 engine/decisions.py 的关系：
  - decisions.py 是「内置库」：唐史切片现有行为，保留不动；
  - 本模块是「数据路径」：读包里的 rules，产出同样结构的 Decision。

两条路最终都落到同一个表与同一套前端结构，所以可以逐条迁移：
包里有规则，就由数据产生；包里没写，仍走内置库。
"""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy.orm import Session

from ..models import Branch
from ..worldpack import PackError, load_decisions
from .decisions import Decision, _has_recent, _make
from .rules_engine import State, conditions_met

#: focus 策略：把 {region} 解析成哪个地区
FOCUS_STRATEGIES = {
    "worst_unrest": lambda state: (state.worst_unrest().region_key if state.worst_unrest() else None),
    "first_region": lambda state: state.regions[0].region_key if state.regions else None,
}


def _resolve_focus(rule: dict[str, Any], state: State) -> str | None:
    focus = rule.get("focus") or {}
    explicit = focus.get("region")
    if explicit:
        return explicit
    strategy = focus.get("strategy")
    if not strategy:
        return None
    impl = FOCUS_STRATEGIES.get(strategy)
    if impl is None:
        raise PackError(
            f"decisions.json 的 focus.strategy='{strategy}' 不认识。"
            f"可用：{', '.join(sorted(FOCUS_STRATEGIES))}"
        )
    return impl(state)


def _render(text: str, *, state: State, focus_region: str | None) -> str:
    """把 {region} 之类替换成实际名字。未知占位符原样保留，不静默吞掉。"""
    if not text:
        return ""
    mapping = {
        "region": (state.by_key[focus_region].name if focus_region in state.by_key else ""),
        "month": str(state.month),
    }
    out = text
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", value)
    return out


def raise_pack_decisions(
    session: Session,
    branch: Branch,
    *,
    world_key: str,
    state: State,
    rng: random.Random | None = None,
) -> list[Decision]:
    """按包里的规则产出决策。包里没有 decisions.json 就返回空。"""
    pack = load_decisions(world_key)
    rules = pack.get("rules") or []
    if not rules:
        return []

    made: list[Decision] = []
    for index, rule in enumerate(rules):
        kind = rule["kind"]
        cooldown = int(rule.get("cooldown_months", 0))
        if cooldown > 0 and _has_recent(
            session, branch.id, kind, branch.tick_index - cooldown
        ):
            continue
        if rng is not None:
            state.roll = rng.random  # 供 chance 条件使用
        if not conditions_met(state, rule.get("when") or {}):
            continue

        focus = _resolve_focus(rule, state)
        options = []
        for opt in rule.get("options") or []:
            options.append(
                {
                    "key": opt.get("key") or opt.get("label"),
                    "label": _render(opt.get("label", ""), state=state, focus_region=focus),
                    "note": _render(opt.get("note", ""), state=state, focus_region=focus),
                    "effects": opt.get("effects") or {},
                    "outcome": opt.get("outcome"),
                }
            )
        if not options:
            raise PackError(f"decisions.json 的 rules[{index}]（{kind}）没有 options")

        decision = _make(
                branch,
                kind=kind,
                title=_render(rule.get("title", kind), state=state, focus_region=focus),
                description=_render(
                    rule.get("description", ""), state=state, focus_region=focus
                ),
                options=options,
                ref_key=rule.get("ref_key"),
                focus_region=focus,
        )
        # 与内置库一致：决策必须落库，否则界面读不到
        session.add(decision)
        made.append(decision)

    if made:
        session.flush()
    return made


__all__ = ["raise_pack_decisions", "FOCUS_STRATEGIES"]