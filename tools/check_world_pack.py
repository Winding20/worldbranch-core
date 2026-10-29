"""校验世界包：结构、字段、权重，以及引用的 key 是否真实存在。

    python tools/check_world_pack.py              # 校验全部
    python tools/check_world_pack.py tang_755     # 只校验一个

校验不通过时退出码非 0，CI 会拦下。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.app_factory import create_app, get_db  # noqa: E402
from app.models import Character, Faction, Region, World  # noqa: E402
from app.worldpack import (  # noqa: E402
    PACK_ROOT,
    PackError,
    load_rubric,
    load_world,
    pack_dir,
)

#: 模板目录与共享规则目录不参与校验（后者没有 world.json）
SKIP = {"_template", "_common"}


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self.checked = 0

    def ok(self, msg: str) -> None:
        print(f"  ✓ {msg}")

    def bad(self, msg: str) -> None:
        print(f"  ✗ {msg}")
        self.failed += 1


def _keys_of(session, model, world_id: int, column: str) -> set[str]:
    return {
        getattr(row, column)
        for row in session.scalars(select(model).where(model.world_id == world_id))
    }


def check_one(key: str, session, report: Report) -> None:
    print(f"\n[{key}]")
    report.checked += 1

    if not pack_dir(key).is_dir():
        report.bad(f"目录不存在：{pack_dir(key)}")
        return

    try:
        world = load_world(key)
        rubric = load_rubric(key)
    except PackError as exc:
        report.bad(str(exc))
        return
    report.ok("world.json / rubric.json 结构合法")

    if world["horizon_months"] != rubric["horizon_months"]:
        report.bad(
            f"world.json 的 horizon_months={world['horizon_months']} 与 "
            f"rubric.json 的 {rubric['horizon_months']} 不一致"
        )
    else:
        report.ok(f"期限一致：{world['horizon_months']} 个月")

    if not rubric["metrics"]:
        report.bad("rubric.json 没有 metrics")
    else:
        total = sum(m["weight"] for m in rubric["metrics"])
        report.ok(f"权重之和 {total:.3f}")

    # ---- 引用的 key 是否存在 ----
    row = session.scalar(select(World).where(World.key == key))
    if row is None:
        print(f"  · 数据库里没有这个世界（尚未播种），跳过引用检查")
        return

    regions = _keys_of(session, Region, row.id, "key")
    factions = _keys_of(session, Faction, row.id, "key")
    characters = _keys_of(session, Character, row.id, "key")

    def want(kind: str, value: str, pool: set[str], where: str) -> None:
        if not value:
            return
        if value in pool:
            return
        report.bad(f"{where} 引用了不存在的 {kind}：'{value}'")

    for i, spec in enumerate(rubric["metrics"]):
        where = f"metrics[{i}]（{spec.get('label')}）"
        for region in spec.get("regions") or []:
            want("地区", region, regions, where)
        want("派系", spec.get("faction", ""), factions, where)
        if spec.get("kind") == "rebellion_status":
            want("人物", spec.get("leader", ""), characters, where)

    for name, spec in (rubric.get("facts") or {}).items():
        where = f"facts.{name}"
        for region in spec.get("regions") or []:
            want("地区", region, regions, where)
        want("派系", spec.get("faction", ""), factions, where)
        want("人物", spec.get("character", ""), characters, where)

    unknown = [
        k
        for k in (rubric.get("metrics") or [])
        if k.get("kind") not in _known_kinds()
    ]
    if unknown:
        report.bad(f"未知的指标类型：{', '.join(u.get('kind', '?') for u in unknown)}")
    else:
        report.ok("指标类型均可用")


def _known_kinds() -> set[str]:
    from app.worldpack import KNOWN_METRIC_KINDS

    return set(KNOWN_METRIC_KINDS)


def main(argv: list[str]) -> int:
    app = create_app()
    report = Report()

    targets = argv[1:] or [
        p.name for p in sorted(PACK_ROOT.iterdir()) if p.is_dir() and p.name not in SKIP
    ]
    if not targets:
        print("没有找到任何世界包")
        return 1

    with app.app_context(), get_db().session() as session:
        for key in targets:
            check_one(key, session, report)

    print()
    if report.failed:
        print(f"✗ {report.failed} 项未通过（共检查 {report.checked} 个世界包）")
        return 1
    print(f"✓ 全部通过（共检查 {report.checked} 个世界包）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))