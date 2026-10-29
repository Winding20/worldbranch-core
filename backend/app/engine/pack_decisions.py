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
from .expr import build_env, evaluate, expression_of, is_expression
from .rules_engine import State, conditions_met

#: focus 策略：把 {region} 解析成哪个地区
FOCUS_STRATEGIES = {
    "worst_unrest": lambda state: (state.worst_unrest().region_key if state.worst_unrest() else None),
    "first_region": lambda state: state.regions[0].region_key if state.regions else None,
    "max_price": lambda state: (
        max(state.regions, key=lambda r: r.price_index).region_key if state.regions else None
    ),
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


def _render(
    text: str,
    *,
    state: State,
    focus_region: str | None,
    variables: dict[str, float] | None = None,
) -> str:
    """把 {region}/{month}/{变量} 替换成实际值，数字带千分位。"""
    if not text:
        return ""
    mapping: dict[str, str] = {
        "region": (state.by_key[focus_region].name if focus_region in state.by_key else ""),
        "month": str(state.month),
    }
    for name, value in (variables or {}).items():
        if float(value).is_integer():
            mapping[name] = f"{int(value):,}"
        else:
            mapping[name] = f"{value:,.2f}"
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
        env = build_env(
            month=state.month,
            regions_by_key=state.by_key,
            focus_region=focus,
            factions=state.factions,
        )
        # 规则里的 vars：一组具名的中间量，供 detail 与 effects 引用
        variables: dict[str, float] = {}
        for name, value in (rule.get("vars") or {}).items():
            # 逐个求值并立刻放进 env：后面的变量可以引用前面的
            variables[name] = evaluate(
                value if isinstance(value, str) else str(value), env
            )
            env[name] = variables[name]

        options = []
        for opt in rule.get("options") or []:
            options.append(
                {
                    "key": opt.get("key") or opt.get("label"),
                    "label": _render(
                        opt.get("label", ""), state=state, focus_region=focus, variables=variables
                    ),
                    "detail": _render(
                        opt.get("detail", opt.get("note", "")),
                        state=state,
                        focus_region=focus,
                        variables=variables,
                    ),
                    "note": _render(
                        opt.get("note", ""), state=state, focus_region=focus, variables=variables
                    ),
                    "effects": _resolve_effects(opt.get("effects") or {}, env),
                    "outcome": opt.get("outcome"),
                }
            )
        if not options:
            raise PackError(f"decisions.json 的 rules[{index}]（{kind}）没有 options")

        decision = _make(
                branch,
                kind=kind,
                title=_render(
                    rule.get("title", kind),
                    state=state,
                    focus_region=focus,
                    variables=variables,
                ),
                description=_render(
                    rule.get("description", ""),
                    state=state,
                    focus_region=focus,
                    variables=variables,
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


def _resolve_effects(effects: dict[str, Any], env: dict[str, float]) -> dict[str, Any]:
    """把 effects 里的表达式算成数字。嵌套结构（按地区/派系的映射）原样保留。"""
    #: 这些键是「指向谁」，不是数值，不能当表达式算
    SELECTORS = {"target", "region", "character", "army", "faction"}

    def resolve(value: Any, name: str) -> Any:
        if name in SELECTORS:
            return value
        if is_expression(value):
            number = evaluate(expression_of(value), env)
            # 钱和兵是整数概念，别把 896598.37 贯这种小数带进界面
            return int(round(number)) if name in {"treasury", "troops"} else number
        if isinstance(value, dict):
            return {k: resolve(v, k) for k, v in value.items()}
        return value

    return {key: resolve(value, key) for key, value in effects.items()}


__all__ = ["raise_pack_decisions", "FOCUS_STRATEGIES"]