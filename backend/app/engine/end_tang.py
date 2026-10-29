"""唐朝切片的结局入口。

具体规则已经从代码里搬到世界包：seed/packs/tang_755/rubric.json。
这里只做「找到包 → 交给通用求值器」，不再出现任何唐史字段。

想改评分（指标、权重、等级文案），改 JSON 即可，不用动 Python。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Branch
from ..worldpack import load_rubric
from .ending_rubric import evaluate_rubric

#: 本世界的 key（rubric.json 与 world.json 都放在 packs/tang_755 下）
WORLD_KEY = "tang_755"


def evaluate_tang_ending(session: Session, branch: Branch) -> dict[str, Any] | None:
    """八年之后的局势。未到时限返回 None。"""
    rubric = load_rubric(WORLD_KEY)
    return evaluate_rubric(session, branch, rubric)


__all__ = ["evaluate_tang_ending", "WORLD_KEY"]