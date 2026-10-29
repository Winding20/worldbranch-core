"""把 Python 播种器的内容导出成世界包的数据。

做法不是读常量猜字段，而是**真的播一次种**（内存 SQLite），再从表里导出——
这样导出结果与运行时完全一致，不会因为漏了某个字段而悄悄变样。

    python tools/export_pack.py tang_755
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..models import (
    Army,
    Base,
    Character,
    Faction,
    FactionRelation,
    HistoricalEvent,
    KnowledgeDoc,
    Region,
    TradeLink,
)
from . import WORLD_SEEDERS


def _plain(value: Any) -> Any:
    """把 date / 其他不可 JSON 化的值转成朴素类型。"""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _row_dict(row: Any, *, drop: set[str]) -> dict[str, Any]:
    return {
        column.name: _plain(getattr(row, column.name))
        for column in row.__table__.columns
        if column.name not in drop
    }


def dump_world_content(key: str) -> dict[str, Any]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        world = WORLD_SEEDERS[key](session)
        session.flush()

        regions = list(session.scalars(select(Region).where(Region.world_id == world.id)))
        factions = list(
            session.scalars(select(Faction).where(Faction.world_id == world.id))
        )
        relations = list(
            session.scalars(
                select(FactionRelation).where(FactionRelation.world_id == world.id)
            )
        )
        trades = list(
            session.scalars(select(TradeLink).where(TradeLink.world_id == world.id))
        )
        characters = list(
            session.scalars(select(Character).where(Character.world_id == world.id))
        )
        armies = list(session.scalars(select(Army).where(Army.world_id == world.id)))
        events = list(
            session.scalars(
                select(HistoricalEvent).where(HistoricalEvent.world_id == world.id)
            )
        )

        geo: dict[str, Any] = {}
        region_items = []
        for region in regions:
            item = _row_dict(region, drop={"id", "world_id"})
            if item.get("geo"):
                geo[region.key] = item.pop("geo")
            else:
                item.pop("geo", None)
            region_items.append(item)

        # 粮道是双向写的，每对只留一条
        seen: set[tuple[str, str]] = set()
        pairs = []
        for link in trades:
            pair = tuple(sorted((link.from_key, link.to_key)))
            if pair in seen:
                continue
            seen.add(pair)
            pairs.append([link.from_key, link.to_key, link.capacity])

        data: dict[str, Any] = {
            "_comment": f"由 tools/export_pack.py 从 {key} 的播种器导出，可手工继续修改。",
            "world": {
                "name": world.name,
                "era": world.era,
                "description": world.description,
                "start_date": _plain(world.start_date),
                "provenance": world.provenance,
                "scenario": world.scenario,
            },
            "regions": region_items,
            "factions": [_row_dict(f, drop={"id", "world_id"}) for f in factions],
            "relations": [_row_dict(r, drop={"id", "world_id"}) for r in relations],
            "trade_pairs": pairs,
            "characters": [_row_dict(c, drop={"id", "world_id"}) for c in characters],
            "armies": [_row_dict(a, drop={"id", "world_id"}) for a in armies],
            "events": [_row_dict(e, drop={"id", "world_id"}) for e in events],
        }

        docs = list(
            session.scalars(select(KnowledgeDoc).where(KnowledgeDoc.world_id == world.id))
        )
        if docs:
            data["knowledge"] = [
                {
                    "slug": d.slug,
                    "title": d.title,
                    "source": d.source,
                    "reliability": d.reliability,
                    "content": d.content,
                }
                for d in docs
            ]

        data["_geo"] = geo
        return data