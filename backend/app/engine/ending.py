"""天宝九年（tang_750）的五年结局。

规则已搬到世界包 seed/packs/tang_750/rubric.json，这里只做「读包 → 求值」，
外加这个沙盘特有的事实记录（做过多少决策、发生过多少事件）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Branch, Decision, SimEvent
from ..worldpack import load_rubric
from .ending_rubric import _Ctx, evaluate_rubric

#: 本世界的 key（rubric.json 在 packs/tang_750 下）
WORLD_KEY = "tang_750"


def evaluate_ending(session: Session, branch: Branch) -> dict[str, Any] | None:
    """返回结局；未到期限（五年）则返回 None。"""
    rubric = load_rubric(WORLD_KEY)
    result = evaluate_rubric(session, branch, rubric)
    if result is None:
        return None

    ctx = _Ctx(session, branch)
    decisions_made = len(
        list(
            session.scalars(
                select(Decision.id).where(
                    Decision.branch_id == branch.id, Decision.status == "resolved"
                )
            )
        )
    )
    event_count = len(
        list(
            session.scalars(
                select(SimEvent.id).where(SimEvent.branch_id == branch.id)
            )
        )
    )
    regions = ctx.regions
    relation = ctx.relations.get("fanzhen")

    # 这个沙盘特有的事实（不参与评分，供界面与 AI 分析引用）
    result["metrics"].update(
        {
            "allegiance": round(relation.value if relation is not None else 0.5, 3),
            "avg_price": round(
                sum(r.price_index for r in regions) / max(len(regions), 1), 3
            ),
            "max_unrest": round(max((r.unrest for r in regions), default=0.0), 3),
            "decisions_made": decisions_made,
            "events": event_count,
        }
    )
    return result


__all__ = ["evaluate_ending", "WORLD_KEY"]