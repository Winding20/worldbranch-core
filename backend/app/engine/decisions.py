"""决策：世界主动向用户提出的问题。

设计目的：让"用户不知道该干什么"变成"世界告诉你要决定什么"。
每道决策都带 2–3 个选项，每个选项写明代价与后果，选完立即生效并留下事件。
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ArmyState,
    Branch,
    CharacterState,
    Decision,
    FactionState,
    RegionState,
    RelationState,
    SimEvent,
)
from ..utils import format_date
from .counsel import attach_advice
from .offices import SINECURE, apply_office
from .military import candidates_for_command

#: 同一类决策在这段时间内不重复提出（月）
COOLDOWN_TICKS = 12


# --------------------------------------------------------------------------- #
# 触发
# --------------------------------------------------------------------------- #
def _first_region(by_key: dict, keys: tuple[str, ...]) -> str | None:
    """从候选里挑第一个真实存在的地点，找不到就不聚光。"""
    for key in keys:
        if key in by_key:
            return key
    return None


def _has_recent(
    session: Session, branch_id: int, kind: str, tick: int, key: str | None = None
) -> bool:
    """同类决策在冷却期内是否已经提过；给了 key 就按对象分开算。"""
    stmt = select(Decision.id).where(
        Decision.branch_id == branch_id,
        Decision.kind == kind,
        Decision.tick_index >= tick - COOLDOWN_TICKS,
    )
    if key is not None:
        stmt = stmt.where(Decision.ref_key == key)
    return session.scalar(stmt.limit(1)) is not None


def _get_relation(relations: dict[str, RelationState], key: str) -> RelationState | None:
    return relations.get(key)


def raise_decisions(
    session: Session,
    branch: Branch,
    regions: list[RegionState],
    factions: dict[str, FactionState],
    relations: dict[str, RelationState],
    shocked_keys: set[str] | None = None,
    rng: random.Random | None = None,
    characters: dict[str, CharacterState] | None = None,
    armies: dict[str, ArmyState] | None = None,
) -> list[Decision]:
    """按当前局势决定要不要向用户提问题。

    ``shocked_keys`` 是本轮发生过灾害的道，用于触发赈济；
    ``rng`` 由引擎按 (seed, tick) 派生，保证同一分支可复现。
    """
    created: list[Decision] = []
    tick = branch.tick_index
    shocked_keys = shocked_keys or set()
    characters = characters or {}
    armies = armies or {}
    rng = rng or random.Random(f"{branch.seed}:{tick}:decisions")
    by_key = {r.region_key: r for r in regions}
    fanzhen = factions.get("fanzhen")
    tang = factions.get("tang")
    allegiance = _get_relation(relations, "fanzhen")

    # 1) 藩镇请饷：已迁到世界包 seed/packs/tang_750/decisions.json
    #    （数据驱动路径见 engine/pack_decisions.py）
    # 2) 赈济：已迁到共享规则 seed/packs/_common/decisions.json

    # 已迁到共享规则 seed/packs/_shared/tang_warlord.json

    # 已迁到共享规则 seed/packs/_shared/tang_warlord.json

    # 5) 漕运：已迁到共享规则 seed/packs/_common/decisions.json

    # 已迁到共享规则 seed/packs/_shared/tang_warlord.json

    # 7) 任将、8) 赏功：已迁到共享规则 seed/packs/_shared/tang_warlord.json
    #    （按对象逐条生成，用 each + options_from）

    # 决策落库后立刻让朝臣开口：有话说就说，没人可问就留空。
    # 这段收尾以前写在分支末尾，迁移时被一并删掉过——别再去掉它。
    for decision in created:
        attach_advice(decision, characters, armies)
    return created


def _make(
    branch: Branch,
    *,
    kind: str,
    title: str,
    description: str,
    options: list[dict],
    trigger_event_id: int | None = None,
    ref_key: str | None = None,
    focus_region: str | None = None,
) -> Decision:
    return Decision(
        branch_id=branch.id,
        game_date=branch.current_date,
        tick_index=branch.tick_index,
        kind=kind,
        title=title,
        description=description,
        trigger_event_id=trigger_event_id,
        ref_key=ref_key,
        focus_region=focus_region,
        options=options,
        status="pending",
    )


# --------------------------------------------------------------------------- #
# 决议
# --------------------------------------------------------------------------- #
class DecisionError(ValueError):
    pass


def resolve_decision(
    session: Session,
    branch: Branch,
    decision: Decision,
    option_key: str,
) -> SimEvent:
    """执行用户的选择，把它写回世界并留下一条事件。"""
    if decision.status != "pending":
        raise DecisionError("这道决策已经处理过了")

    # 历史岔路口交给 history 处理：它要按"是否照史载"决定后续走向
    if decision.kind == "history_fork":
        from .history import resolve_fork

        return resolve_fork(session, branch, decision, option_key)

    option = next((o for o in (decision.options or []) if o["key"] == option_key), None)
    if option is None:
        raise DecisionError(f"没有这个选项: {option_key}")

    effects: dict[str, Any] = option.get("effects") or {}
    #: 实际发生的变化，用于给用户"选完之后到底怎么了"的即时反馈
    applied: list[dict[str, Any]] = []

    tang = session.scalar(
        select(FactionState).where(
            FactionState.branch_id == branch.id, FactionState.faction_key == "tang"
        )
    )
    if tang is not None:
        if effects.get("treasury"):
            before = tang.treasury
            tang.treasury += float(effects["treasury"])
            applied.append(
                {
                    "label": "朝廷府库",
                    "before": round(before, 0),
                    "after": round(tang.treasury, 0),
                    "unit": "贯",
                }
            )
        if effects.get("legitimacy"):
            before = tang.legitimacy
            tang.legitimacy = min(max(tang.legitimacy + float(effects["legitimacy"]), 0.0), 1.0)
            applied.append(
                {
                    "label": "朝廷威望",
                    "before": round(before, 3),
                    "after": round(tang.legitimacy, 3),
                    "unit": "",
                }
            )

    for key, delta in (effects.get("relation") or {}).items():
        rel = session.scalar(
            select(RelationState).where(
                RelationState.branch_id == branch.id,
                RelationState.from_key == key,
            )
        )
        if rel is not None:
            before = rel.value
            rel.value = min(max(rel.value + float(delta), -1.0), 1.0)
            applied.append(
                {
                    "label": "河北藩镇臣属度",
                    "before": round(before, 3),
                    "after": round(rel.value, 3),
                    "unit": "",
                }
            )

    for key, delta in (effects.get("unrest") or {}).items():
        region = session.scalar(
            select(RegionState).where(
                RegionState.branch_id == branch.id, RegionState.region_key == key
            )
        )
        if region is not None:
            before = region.unrest
            region.unrest = min(max(region.unrest + float(delta), 0.0), 1.0)
            applied.append(
                {
                    "label": f"{region.name}民变",
                    "before": round(before, 3),
                    "after": round(region.unrest, 3),
                    "unit": "",
                }
            )

    for key, delta in (effects.get("grain") or {}).items():
        region = session.scalar(
            select(RegionState).where(
                RegionState.branch_id == branch.id, RegionState.region_key == key
            )
        )
        if region is not None:
            before = region.grain_stock
            region.grain_stock = max(region.grain_stock + float(delta), 0.0)
            applied.append(
                {
                    "label": f"{region.name}存粮",
                    "before": round(before, 0),
                    "after": round(region.grain_stock, 0),
                    "unit": "石",
                }
            )

    for key, delta in (effects.get("tax") or {}).items():
        region = session.scalar(
            select(RegionState).where(
                RegionState.branch_id == branch.id, RegionState.region_key == key
            )
        )
        if region is not None:
            before = region.tax_rate
            region.tax_rate = min(max(region.tax_rate + float(delta), 0.0), 0.5)
            applied.append(
                {
                    "label": f"{region.name}税率",
                    "before": round(before, 3),
                    "after": round(region.tax_rate, 3),
                    "unit": "",
                }
            )

    # 忠诚变化：赏则回心，削则见疑
    for key, delta in (effects.get("loyalty") or {}).items():
        person = session.scalar(
            select(CharacterState).where(
                CharacterState.branch_id == branch.id, CharacterState.character_key == key
            )
        )
        if person is not None:
            before = person.loyalty
            person.loyalty = min(max(person.loyalty + float(delta), 0.0), 1.0)
            applied.append(
                {
                    "label": f"{person.name}忠诚",
                    "before": round(before, 3),
                    "after": round(person.loyalty, 3),
                    "unit": "",
                }
            )

    # 改授官职：实权转闲职，或加官进爵
    for char_key, spec in (effects.get("office") or {}).items():
        person = session.scalar(
            select(CharacterState).where(
                CharacterState.branch_id == branch.id,
                CharacterState.character_key == char_key,
            )
        )
        if person is not None:
            change = apply_office(
                person,
                title=spec["title"],
                rank=int(spec.get("rank", person.office_rank)),
                kind=spec.get("kind", person.office_kind),
            )
            applied.append(
                {
                    "label": f"{person.name}官职",
                    "before": change.before,
                    "after": change.after,
                    "unit": "",
                }
            )

    # 削兵权：解主帅之职，兵额折损
    for army_key in effects.get("disarm") or []:
        army = session.scalar(
            select(ArmyState).where(
                ArmyState.branch_id == branch.id, ArmyState.army_key == army_key
            )
        )
        if army is not None:
            before_troops = army.troops
            army.commander_key = None
            army.troops = int(army.troops * 0.7)
            army.morale = max(army.morale - 0.10, 0.0)
            applied.append(
                {
                    "label": f"{army.name}兵额",
                    "before": before_troops,
                    "after": army.troops,
                    "unit": "",
                }
            )

    # 任命主帅：换人之后，这支军队的战力会立刻变
    for army_key, person_key in (effects.get("commander") or {}).items():
        army = session.scalar(
            select(ArmyState).where(
                ArmyState.branch_id == branch.id, ArmyState.army_key == army_key
            )
        )
        person = session.scalar(
            select(CharacterState).where(
                CharacterState.branch_id == branch.id,
                CharacterState.character_key == person_key,
            )
        )
        if army is not None and person is not None:
            before = army.commander_key or "（空缺）"
            army.commander_key = person_key
            applied.append(
                {
                    "label": f"{army.name}主帅",
                    "before": before,
                    "after": person.name,
                    "unit": "",
                }
            )

    for army_key, delta in (effects.get("morale") or {}).items():
        army = session.scalar(
            select(ArmyState).where(
                ArmyState.branch_id == branch.id, ArmyState.army_key == army_key
            )
        )
        if army is not None:
            before = army.morale
            army.morale = min(max(army.morale + float(delta), 0.0), 1.0)
            applied.append(
                {
                    "label": f"{army.name}士气",
                    "before": round(before, 3),
                    "after": round(army.morale, 3),
                    "unit": "",
                }
            )

    event = SimEvent(
        branch_id=branch.id,
        game_date=branch.current_date,
        tick_index=branch.tick_index,
        scope="nation",
        kind="decision",
        severity=4,
        duration_months=1,
        title=f"决策：{decision.title} → {option['label']}",
        summary=option.get("detail") or "",
        region_keys=[],
        faction_keys=["tang"],
        causes=(
            [{"event_id": decision.trigger_event_id, "rel": "caused_by"}]
            if decision.trigger_event_id
            else [{"rule": "decision", "kind": decision.kind}]
        ),
        metrics={"decision_kind": decision.kind, "option": option_key, "applied": applied},
    )
    session.add(event)
    session.flush()

    decision.status = "resolved"
    decision.resolved_option = option_key
    decision.resolved_at = dt.datetime.now(dt.timezone.utc)
    decision.result_event_id = event.id
    session.flush()
    return event


def pending_decisions(session: Session, branch_id: int) -> list[Decision]:
    return list(
        session.scalars(
            select(Decision)
            .where(Decision.branch_id == branch_id, Decision.status == "pending")
            .order_by(Decision.tick_index.desc(), Decision.id.desc())
        )
    )


def decision_history(session: Session, branch_id: int, limit: int = 50) -> list[Decision]:
    return list(
        session.scalars(
            select(Decision)
            .where(Decision.branch_id == branch_id)
            .order_by(Decision.id.desc())
            .limit(limit)
        )
    )


def describe_relation(session: Session, branch_id: int) -> list[dict]:
    """把关系翻译成人能读的话。"""
    rows = list(
        session.scalars(
            select(RelationState).where(RelationState.branch_id == branch_id)
        )
    )
    out = []
    for rel in rows:
        value = rel.value
        if value >= 0.6:
            label = "恭顺"
        elif value >= 0.35:
            label = "听命"
        elif value >= 0.2:
            label = "阳奉阴违"
        elif value >= 0.0:
            label = "离心"
        else:
            label = "敌视"
        out.append({**rel.snapshot(), "label": label, "date": format_date(dt.date.today())})
    return out