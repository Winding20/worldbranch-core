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

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Branch
from ..worldpack import PackError, load_decisions, load_world
from .decisions import Decision, _has_recent, _make
from .expr import build_env, evaluate, expression_of, is_expression
from .rule_generators import GENERATORS
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
        if isinstance(value, str):
            # 对象名之类，原样放进去
            mapping[name] = value
            continue
        if float(value).is_integer():
            mapping[name] = f"{int(value):,}"
        else:
            mapping[name] = f"{value:,.2f}"
    out = text
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", value)
    return out


#: each.entity 可遍历的实体表
ENTITIES = {
    "army": lambda state: state.armies,
    "character": lambda state: state.characters,
}


def _each_matches(
    entity: Any, where: dict[str, Any], *, player: str, state: Any = None
) -> bool:
    """each.where 的过滤条件：够用即可，不认识的条件直接报错。"""
    for name, expected in (where or {}).items():
        if name == "faction":
            want = player if expected == "player" else expected
            if getattr(entity, "faction_key", None) != want:
                return False
        elif name == "role_not":
            if getattr(entity, "role", None) == expected:
                return False
        elif name == "alive":
            if bool(getattr(entity, "alive", True)) != bool(expected):
                return False
        elif name == "commander_missing":
            if bool(expected) and getattr(entity, "commander_key", None):
                return False
        elif name == "no_living_commander":
            key = getattr(entity, "commander_key", None)
            person = state.characters.get(key) if (state is not None and key) else None
            if bool(expected) and person is not None and person.alive:
                return False
        elif name == "raised":
            if bool(getattr(entity, "raised", True)) != bool(expected):
                return False
        elif name == "loyalty_at_most":
            if float(getattr(entity, "loyalty", 1.0)) > float(expected):
                return False
        elif name == "loyalty_above":
            if float(getattr(entity, "loyalty", 0.0)) <= float(expected):
                return False
        elif name == "has_troops":
            if bool(expected) and not any(
                a.commander_key == getattr(entity, "character_key", None)
                for a in entity.__dict__.get("_armies", [])
            ):
                pass  # 由调用方进一步过滤，这里放行
        else:
            raise PackError(f"each.where 里未知的过滤条件：'{name}'")
    return True


def _recently_asked(
    session: Session, branch_id: int, kind: str, since_tick: int, key: str | None = None
) -> bool:
    """同一类（同一对象）在冷却期内是否已经问过。"""
    stmt = select(Decision.id).where(
        Decision.branch_id == branch_id,
        Decision.kind == kind,
        Decision.tick_index >= since_tick,
    )
    if key is not None:
        stmt = stmt.where(Decision.ref_key == key)
    return session.scalar(stmt.limit(1)) is not None


def _player_faction(world_key: str) -> str:
    try:
        return load_world(world_key).get("player_faction") or "tang"
    except PackError:
        return "tang"


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

    player = _player_faction(world_key)
    if rng is not None:
        state.roll = rng.random  # 供 chance 条件使用

    made: list[Decision] = []
    for index, rule in enumerate(rules):
        kind = rule["kind"]
        cooldown = int(rule.get("cooldown_months", 12))

        # each：对每一个符合条件的东西各问一次（军队、人物……）
        targets: list[Any] = [None]
        each = rule.get("each")
        if each:
            table = ENTITIES.get(each.get("entity", ""))
            if table is None:
                raise PackError(
                    f"rules[{index}]（{kind}）的 each.entity 不认识："
                    f"'{each.get('entity')}'。可用：{', '.join(sorted(ENTITIES))}"
                )
            targets = [
                e
                for e in table(state).values()
                if _each_matches(e, each.get("where") or {}, player=player, state=state)
            ]
            if not targets:
                continue

        for entity in targets:
            ref_key = rule.get("ref_key")
            if entity is not None and ref_key == "each.key":
                ref_key = getattr(entity, "army_key", None) or getattr(
                    entity, "character_key", None
                )
            elif entity is not None:
                ref_key = ref_key or getattr(entity, "army_key", None) or getattr(
                    entity, "character_key", None
                )

            if cooldown > 0 and _recently_asked(
                session, branch.id, kind, branch.tick_index - cooldown, ref_key
            ):
                continue
            if not conditions_met(state, rule.get("when") or {}):
                continue

            focus = _resolve_focus(rule, state)
            env = build_env(
                month=state.month,
                regions_by_key=state.by_key,
                focus_region=focus,
                factions=state.factions,
            )
            env["player_faction"] = player  # 生成器要按玩家派系筛人
            variables: dict[str, float] = {}
            for name, value in (rule.get("vars") or {}).items():
                variables[name] = evaluate(
                    value if isinstance(value, str) else str(value), env
                )
                env[name] = variables[name]

            # 渲染用的变量：规则 vars + 当前对象
            render_vars: dict[str, Any] = dict(variables)
            if entity is not None:
                name = getattr(entity, "name", "") or ref_key or ""
                render_vars["each"] = name
                render_vars["each_key"] = ref_key or ""

            raw_options = rule.get("options") or []
            if rule.get("options_from"):
                gen = GENERATORS.get(rule["options_from"])
                if gen is None:
                    raise PackError(
                        f"rules[{index}]（{kind}）的 options_from 不认识："
                        f"'{rule['options_from']}'。可用：{', '.join(sorted(GENERATORS))}"
                    )
                raw_options = gen(entity, state, env)
            if not raw_options:
                continue

            options = [
                {
                    "key": opt.get("key") or opt.get("label"),
                    "label": _render(
                        opt.get("label", ""),
                        state=state,
                        focus_region=focus,
                        variables=render_vars,
                    ),
                    "detail": _render(
                        opt.get("detail", opt.get("note", "")),
                        state=state,
                        focus_region=focus,
                        variables=render_vars,
                    ),
                    "note": _render(
                        opt.get("note", ""),
                        state=state,
                        focus_region=focus,
                        variables=render_vars,
                    ),
                    "effects": _resolve_effects(opt.get("effects") or {}, env),
                    "outcome": opt.get("outcome"),
                }
                for opt in raw_options
            ]

            if entity is not None and not rule.get("focus", {}).get("region"):
                focus = getattr(entity, "region_key", None) or focus

            decision = _make(
                branch,
                kind=kind,
                title=_render(
                    rule.get("title", kind),
                    state=state,
                    focus_region=focus,
                    variables=render_vars,
                ),
                description=_render(
                    rule.get("description", ""),
                    state=state,
                    focus_region=focus,
                    variables=render_vars,
                ),
                options=options,
                ref_key=ref_key,
                focus_region=focus,
            )
            session.add(decision)
            made.append(decision)

    if made:
        session.flush()
    return made

def _resolve_effects(effects: dict[str, Any], env: dict[str, float]) -> dict[str, Any]:
    """把 effects 里的表达式算成数字。嵌套结构（按地区/派系的映射）原样保留。"""
    #: 这些键是「指向谁」，不是数值，不能当表达式算
    SELECTORS = {"target", "region", "character", "army", "faction"}
    #: 这些效果的值本身就是 key（派系/人物），不是数字
    LITERAL_MAPS = {"commander", "control", "autonomy", "office", "disarm"}

    def resolve(value: Any, name: str) -> Any:
        if name in SELECTORS or name in LITERAL_MAPS:
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