"""通用结局评估：读世界包里的 rubric，算出分数与评级。

引擎不再认识「两京」「河北」这些概念——它们只存在于包里。
新增一段历史，只需写一份 rubric.json。
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Branch, CharacterState, FactionState, RegionState, RelationState
from ..utils import format_date
from ..worldpack import PackError, load_rubric


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(value, high))


class _Ctx:
    """一次评估需要的全部状态，算一次、供所有指标复用。"""

    def __init__(self, session: Session, branch: Branch) -> None:
        self.regions = list(
            session.scalars(select(RegionState).where(RegionState.branch_id == branch.id))
        )
        self.by_key = {r.region_key: r for r in self.regions}
        self.factions = {
            f.faction_key: f
            for f in session.scalars(
                select(FactionState).where(FactionState.branch_id == branch.id)
            )
        }
        self.characters = {
            c.character_key: c
            for c in session.scalars(
                select(CharacterState).where(CharacterState.branch_id == branch.id)
            )
        }
        self.relations = {
            r.from_key: r
            for r in session.scalars(
                select(RelationState).where(RelationState.branch_id == branch.id)
            )
        }

    def regions_of(self, keys: list[str]) -> list[RegionState | None]:
        return [self.by_key.get(k) for k in keys]


# ---------------- 指标实现 ----------------
# 每个函数签名统一：(_Ctx, spec) -> 0..100 的分数


def _regions_held(ctx: _Ctx, spec: dict[str, Any]) -> float:
    keys = spec.get("regions") or []
    faction = spec.get("faction")
    if not keys:
        return 0.0
    held = sum(
        1
        for r in ctx.regions_of(keys)
        if r is not None and r.faction_key == faction
    )
    return held / len(keys) * 100


def _rebellion_status(ctx: _Ctx, spec: dict[str, Any]) -> float:
    faction = spec.get("faction")
    scores = spec.get("scores") or {"over": 100, "leader_gone": 60, "ongoing": 20}
    hostile = [r for r in ctx.regions if r.faction_key != faction]
    if not hostile:
        return float(scores["over"])
    leader_key = spec.get("leader")
    leader = ctx.characters.get(leader_key) if leader_key else None
    if leader is not None and not leader.alive:
        return float(scores["leader_gone"])
    return float(scores["ongoing"])


def _faction_treasury(ctx: _Ctx, spec: dict[str, Any]) -> float:
    fac = ctx.factions.get(spec.get("faction"))
    treasury = fac.treasury if fac else 0.0
    offset = float(spec.get("offset", 0))
    span = float(spec.get("span", 1)) or 1.0
    return _clamp((treasury + offset) / span) * 100


def _faction_field(ctx: _Ctx, spec: dict[str, Any]) -> float:
    fac = ctx.factions.get(spec.get("faction"))
    field = spec.get("field", "legitimacy")
    value = getattr(fac, field, 0.0) if fac else 0.0
    return _clamp(float(value)) * 100


def _max_unrest(ctx: _Ctx, spec: dict[str, Any]) -> float:
    span = float(spec.get("span", 1.0)) or 1.0
    worst = max((r.unrest for r in ctx.regions), default=0.0)
    value = 1 - _clamp(worst / span)
    if spec.get("invert", True):
        return value * 100
    return (1 - value) * 100


def _region_control(ctx: _Ctx, spec: dict[str, Any]) -> float:
    """地区实际控制权：直辖 / 名义归附而自专 / 沦陷。"""
    keys = spec.get("regions") or []
    faction = spec.get("faction")
    scores = spec.get("scores") or {"held": 100, "autonomous": 40, "lost": 0}
    if not keys:
        return 0.0
    if any(r is None for r in ctx.regions_of(keys)):
        return float(scores["lost"])
    if any(r.faction_key != faction for r in ctx.regions_of(keys)):
        return float(scores["lost"])
    if any(getattr(r, "autonomous", False) for r in ctx.regions_of(keys)):
        return float(scores["autonomous"])
    return float(scores["held"])


def _unrest_pressure(ctx: _Ctx, spec: dict[str, Any]) -> float:
    """民心：平均与峰值加权。一个地区烧起来，不能被其它地区平均掉。"""
    avg = sum(r.unrest for r in ctx.regions) / max(len(ctx.regions), 1)
    peak = max((r.unrest for r in ctx.regions), default=0.0)
    aw = float(spec.get("avg_weight", 0.6))
    mw = float(spec.get("max_weight", 0.4))
    span = float(spec.get("span", 0.6)) or 1.0
    pressure = avg * aw + peak * mw
    return (1 - _clamp(pressure / span)) * 100


def _avg_price(ctx: _Ctx, spec: dict[str, Any]) -> float:
    avg = sum(r.price_index for r in ctx.regions) / max(len(ctx.regions), 1)
    base = float(spec.get("base", 0.8))
    span = float(spec.get("span", 2.2)) or 1.0
    return (1 - _clamp((avg - base) / span)) * 100


def _relation_value(ctx: _Ctx, spec: dict[str, Any]) -> float:
    """与某派系的关系值（-1..1）折算成 0..100。"""
    key = spec.get("from_key") or spec.get("faction")
    rel = ctx.relations.get(key)
    value = rel.value if rel is not None else float(spec.get("default", 0.5))
    span = float(spec.get("span", 0.7)) or 1.0
    return _clamp(value / span) * 100


METRICS: dict[str, Callable[[_Ctx, dict[str, Any]], float]] = {
    "regions_held": _regions_held,
    "rebellion_status": _rebellion_status,
    "faction_treasury": _faction_treasury,
    "faction_field": _faction_field,
    "max_unrest": _max_unrest,
    "region_control": _region_control,
    "unrest_pressure": _unrest_pressure,
    "avg_price": _avg_price,
    "relation_value": _relation_value,
}


# ---------------- 事实清单 ----------------
def _fact(ctx: _Ctx, spec: dict[str, Any]) -> Any:
    kind = spec.get("kind")
    faction = spec.get("faction")
    if kind == "count_regions_held":
        keys = spec.get("regions") or []
        return sum(1 for r in ctx.regions_of(keys) if r is not None and r.faction_key == faction)
    if kind == "no_hostile_regions":
        return not any(r.faction_key != faction for r in ctx.regions)
    if kind == "list_hostile_regions":
        return [r.name for r in ctx.regions if r.faction_key != faction]
    if kind == "list_autonomous_regions":
        return [
            r.name
            for r in ctx.regions
            if r.faction_key == faction and getattr(r, "autonomous", False)
        ]
    if kind == "character_alive":
        who = ctx.characters.get(spec.get("character"))
        return bool(who.alive) if who else False
    raise PackError(f"rubric.facts 里的 kind='{kind}' 不认识")


def _grade(rubric: dict[str, Any], score: float) -> tuple[str, str]:
    for row in rubric["grades"]:
        threshold, name, verdict = row
        if score >= threshold:
            return name, verdict
    last = rubric["grades"][-1]
    return last[1], last[2]


def evaluate_rubric(session: Session, branch: Branch, rubric: dict[str, Any]) -> dict[str, Any] | None:
    """未到期限返回 None；到期限返回与旧接口一致的结局结构。"""
    horizon = int(rubric["horizon_months"])
    if branch.tick_index < horizon:
        return None

    ctx = _Ctx(session, branch)

    raw: dict[str, float] = {}
    for spec in rubric["metrics"]:
        impl = METRICS.get(spec["kind"])
        if impl is None:
            raise PackError(f"未实现的指标 kind：{spec['kind']}")
        raw[spec["label"]] = impl(ctx, spec)

    total = round(
        sum(raw[spec["label"]] * float(spec["weight"]) for spec in rubric["metrics"]), 1
    )
    grade, verdict = _grade(rubric, total)

    facts: dict[str, Any] = {}
    for name, spec in (rubric.get("facts") or {}).items():
        facts[name] = _fact(ctx, spec)

    # 玩家派系由 rubric 里的指标决定；取所有指标里出现的第一个派系
    player_key = rubric.get("player_faction") or next(
        (m.get("faction") for m in rubric.get("metrics") or [] if m.get("faction")),
        None,
    )
    tang = ctx.factions.get(player_key) if player_key else None

    return {
        "reached": True,
        "date": format_date(branch.current_date),
        "months": branch.tick_index,
        "score": total,
        "grade": grade,
        "verdict": verdict,
        "scores": {k: round(v, 1) for k, v in raw.items()},
        "metrics": {
            "treasury": round(tang.treasury) if tang else 0,
            "legitimacy": round(tang.legitimacy, 3) if tang else 0,
            **facts,
        },
    }