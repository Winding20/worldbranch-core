"""通用规则引擎：把「什么时候问什么、选了会怎样」变成数据。

世界包里的 decisions.json 用两张词表描述这一切：

  条件（when）  —— 判断这个规则此刻该不该触发
  效果（effects）—— 玩家选了某个选项后，世界怎么变

引擎只认识这两个词表里的原语，不认识任何具体地名或人名。
新增一段历史，只需写 JSON；新增一种判断方式，才需要改这里。
"""

from __future__ import annotations

import random
from typing import Any, Callable

from ..worldpack import PackError
from .decisions import Decision, _has_recent, _make

# ====================================================================
# 条件原语
# ====================================================================
# 签名：(状态, 参数值) -> bool
# 状态是一个简单对象，见 State；参数就是 JSON 里写的值。


class State:
    """一次判定需要看到的世界状态（由调用方组装，便于测试）。"""

    def __init__(
        self,
        *,
        regions: list[Any],
        factions: dict[str, Any],
        characters: dict[str, Any] | None = None,
        armies: dict[str, Any] | None = None,
        month: int = 0,
    ) -> None:
        self.regions = regions
        self.by_key = {r.region_key: r for r in regions}
        self.factions = factions
        self.characters = characters or {}
        self.armies = armies or {}
        self.month = month

    def worst_unrest(self):
        return max(self.regions, key=lambda r: r.unrest, default=None)


def _treasury_below(state: State, value: float) -> bool:
    """任一派系（或指定派系）府库低于阈值。"""
    if isinstance(value, dict):
        fac = state.factions.get(value.get("faction", ""))
        return bool(fac and fac.treasury < float(value["amount"]))
    return any(f.treasury < float(value) for f in state.factions.values())


def _unrest_above(state: State, value: float) -> bool:
    return any(r.unrest > float(value) for r in state.regions)


def _legitimacy_below(state: State, value: float) -> bool:
    return any(f.legitimacy < float(value) for f in state.factions.values())


def _region_held_by(state: State, spec: dict[str, Any]) -> bool:
    region = state.by_key.get(spec.get("region", ""))
    return bool(region and region.faction_key == spec.get("faction"))


def _faction_has_region(state: State, spec: dict[str, Any]) -> bool:
    faction = spec.get("faction")
    return any(r.faction_key == faction for r in state.regions)


def _character_alive(state: State, spec: dict[str, Any]) -> bool:
    who = state.characters.get(spec.get("character", ""))
    return bool(who and who.alive)


def _character_loyalty_below(state: State, spec: dict[str, Any]) -> bool:
    who = state.characters.get(spec.get("character", ""))
    return bool(who and who.loyalty < float(spec.get("value", 0.45)))


def _chance(state: State, value: float) -> bool:
    # 由调用方把随机源放进 state.chance
    roll = getattr(state, "roll", None)
    if roll is None:
        return True
    return roll() < float(value)


CONDITIONS: dict[str, Callable[[State, Any], bool]] = {
    "treasury_below": _treasury_below,
    "unrest_above": _unrest_above,
    "legitimacy_below": _legitimacy_below,
    "region_held_by": _region_held_by,
    "faction_has_region": _faction_has_region,
    "character_alive": _character_alive,
    "character_loyalty_below": _character_loyalty_below,
    "chance": _chance,
}


def conditions_met(state: State, when: dict[str, Any]) -> bool:
    """when 里的条件必须**全部**满足。空 when 视为总是成立。"""
    for name, value in (when or {}).items():
        impl = CONDITIONS.get(name)
        if impl is None:
            raise PackError(
                f"decisions.json 里未知的条件：'{name}'。"
                f"可用：{', '.join(sorted(CONDITIONS))}"
            )
        if not impl(state, value):
            return False
    return True


# ====================================================================
# 效果原语
# ====================================================================
# 签名：(上下文, 参数) -> None；上下文见 apply_effects。

EFFECTS_APPLIED: list[str] = []


def _target_region(ctx: dict[str, Any], spec: Any) -> Any:
    """把 "focus" 之类的占位解析成具体地区对象。

    ctx["focus_region"] 存的是 key（字符串），不是对象——这里要查回对象。
    """
    key = ctx.get("focus_region") if spec in (None, "focus") else spec
    if not key:
        return None
    return ctx["regions_by_key"].get(key)


def _apply_treasury(ctx, value) -> None:
    fac = ctx["player_faction"]
    if fac is not None:
        fac.treasury += float(value)


def _apply_legitimacy(ctx, value) -> None:
    fac = ctx["player_faction"]
    if fac is not None:
        fac.legitimacy = min(max(fac.legitimacy + float(value), 0.0), 1.0)


def _apply_unrest(ctx, spec) -> None:
    """spec 既可以是 {地区: 增量}，也可以是 {target:..., delta:...}。"""
    if isinstance(spec, dict) and "delta" in spec:
        region = _target_region(ctx, spec.get("target"))
        if region is not None:
            region.unrest = max(0.0, region.unrest + float(spec["delta"]))
        return
    for key, delta in (spec or {}).items():
        region = ctx["regions_by_key"].get(key)
        if region is not None:
            region.unrest = max(0.0, region.unrest + float(delta))


def _apply_grain(ctx, spec) -> None:
    if isinstance(spec, dict) and "delta" in spec:
        region = _target_region(ctx, spec.get("target"))
        if region is not None:
            region.grain_stock = max(0.0, region.grain_stock + float(spec["delta"]))
        return
    for key, delta in (spec or {}).items():
        region = ctx["regions_by_key"].get(key)
        if region is not None:
            region.grain_stock = max(0.0, region.grain_stock + float(delta))


def _apply_relation(ctx, spec) -> None:
    for key, delta in (spec or {}).items():
        rel = ctx["relations"].get(key)
        if rel is not None:
            rel.value = min(max(rel.value + float(delta), -1.0), 1.0)


def _apply_autonomy(ctx, spec) -> None:
    for key, value in (spec or {}).items():
        region = ctx["regions_by_key"].get(key)
        if region is not None:
            region.autonomous = bool(value)


def _apply_control(ctx, spec) -> None:
    """地区易手：{地区: 派系}。"""
    for key, faction in (spec or {}).items():
        region = ctx["regions_by_key"].get(key)
        if region is not None:
            region.faction_key = faction


def _apply_loyalty(ctx, spec) -> None:
    if isinstance(spec, dict) and "delta" in spec:
        who = ctx["characters"].get(spec.get("character", ""))
        if who is not None:
            who.loyalty = min(max(who.loyalty + float(spec["delta"]), 0.0), 1.0)
        return
    for key, delta in (spec or {}).items():
        who = ctx["characters"].get(key)
        if who is not None:
            who.loyalty = min(max(who.loyalty + float(delta), 0.0), 1.0)


def _apply_troops(ctx, spec) -> None:
    if isinstance(spec, dict) and "delta" in spec:
        army = ctx["armies"].get(spec.get("army", ""))
        if army is not None:
            army.troops = max(0, army.troops + int(spec["delta"]))
        return
    for key, delta in (spec or {}).items():
        army = ctx["armies"].get(key)
        if army is not None:
            army.troops = max(0, army.troops + int(delta))


def _apply_morale(ctx, spec) -> None:
    for key, delta in (spec or {}).items():
        army = ctx["armies"].get(key)
        if army is not None:
            army.morale = min(max(army.morale + float(delta), 0.0), 1.0)


EFFECTS: dict[str, Callable[[dict[str, Any], Any], None]] = {
    "treasury": _apply_treasury,
    "legitimacy": _apply_legitimacy,
    "unrest": _apply_unrest,
    "grain": _apply_grain,
    "relation": _apply_relation,
    "autonomy": _apply_autonomy,
    "control": _apply_control,
    "loyalty": _apply_loyalty,
    "troops": _apply_troops,
    "morale": _apply_morale,
}


def apply_effects(ctx: dict[str, Any], effects: dict[str, Any]) -> None:
    for name, value in (effects or {}).items():
        impl = EFFECTS.get(name)
        if impl is None:
            raise PackError(
                f"decisions.json 里未知的效果：'{name}'。"
                f"可用：{', '.join(sorted(EFFECTS))}"
            )
        impl(ctx, value)


__all__ = [
    "State",
    "CONDITIONS",
    "EFFECTS",
    "conditions_met",
    "apply_effects",
]