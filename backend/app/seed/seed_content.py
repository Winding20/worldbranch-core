"""从世界包的 content.json 播种一个世界（纯数据，不写 Python）。

内容文件的结构（见 docs/世界包规范.md 第 6 节）：

    {
      "world":  {"name": "...", "era": "...", "description": "...", "start_date": "..."},
      "regions":     [{"key","name","lat","lon","population","faction_key"}],
      "factions":    [{"key","name","kind","capital_region_key","treasury","legitimacy","military"}],
      "relations":   [{"from_key","to_key","kind","value"}],
      "trade_pairs": [["a","b",capacity]],
      "characters":  [{"key","name","role","faction_key","ability","loyalty","ambition",
                       "location_region_key","office","office_rank","office_kind",...}],
      "armies":      [{"key","name","faction_key","commander_key","region_key",
                       "troops","morale","supply","raises_at","raised"}],
      "events":      [{"key","date","title","category","description","conditions",
                       "effects","divergence_note","source","fork"}]
    }

派生值（耕地、存粮）由人口按引擎规则算出来，不用作者填。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..engine.rules import GRAIN_PER_CAPITA_MONTH, SEASON_FACTOR
from ..models import (
    Army,
    Character,
    Faction,
    FactionRelation,
    HistoricalEvent,
    Region,
    TradeLink,
    World,
)
from ..worldpack import load_world, pack_dir


class ContentError(ValueError):
    """content.json 内容不合法。"""


def content_path(world_key: str) -> Path:
    return pack_dir(world_key) / "content.json"


def has_content(world_key: str) -> bool:
    return content_path(world_key).is_file()


def load_content(world_key: str) -> dict[str, Any]:
    path = content_path(world_key)
    if not path.is_file():
        raise ContentError(f"缺少 content.json：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContentError(
            f"content.json 不是合法 JSON（第 {exc.lineno} 行）：{exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ContentError("content.json 顶层必须是对象")

    regions = data.get("regions") or []
    if not regions:
        raise ContentError("content.json 至少要有一个 region")
    region_keys = {r.get("key") for r in regions}
    faction_keys = {f.get("key") for f in (data.get("factions") or [])}
    character_keys = {c.get("key") for c in (data.get("characters") or [])}

    for i, r in enumerate(regions):
        for field in ("key", "name"):
            if not r.get(field):
                raise ContentError(f"regions[{i}] 缺少字段：{field}")
        fac = r.get("faction_key")
        if fac and fac not in faction_keys:
            raise ContentError(f"regions[{i}]（{r['key']}）的 faction_key 不存在：{fac}")

    for i, a in enumerate(data.get("armies") or []):
        if a.get("faction_key") not in faction_keys:
            raise ContentError(f"armies[{i}] 的 faction_key 不存在：{a.get('faction_key')}")
        if a.get("region_key") not in region_keys:
            raise ContentError(f"armies[{i}] 的 region_key 不存在：{a.get('region_key')}")
        cmd = a.get("commander_key")
        if cmd and cmd not in character_keys:
            raise ContentError(f"armies[{i}] 的 commander_key 不存在：{cmd}")

    for i, c in enumerate(data.get("characters") or []):
        if c.get("faction_key") and c["faction_key"] not in faction_keys:
            raise ContentError(f"characters[{i}] 的 faction_key 不存在：{c['faction_key']}")
        loc = c.get("location_region_key")
        if loc and loc not in region_keys:
            raise ContentError(f"characters[{i}] 的 location_region_key 不存在：{loc}")

    for i, e in enumerate(data.get("events") or []):
        for field in ("key", "date", "title"):
            if not e.get(field):
                raise ContentError(f"events[{i}] 缺少字段：{field}")
    return data


def _parse_date(value: str) -> dt.date:
    """世界包里的日期是 "0208-07-01" 这种四位年份的字符串。"""
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ContentError(
            f"start_date 格式应为 YYYY-MM-DD（例如 0208-07-01），实际：{value!r}"
        ) from exc


def seed_content(session: Session, world_key: str) -> World:
    """按包里的数据建世界。可重复调用前需先清空同名世界。"""
    meta = load_world(world_key)
    data = load_content(world_key)

    info = data.get("world") or {}
    world = World(
        key=world_key,
        name=info.get("name") or meta["title"],
        era=info.get("era") or meta.get("subtitle") or "",
        description=info.get("description") or meta.get("role_brief") or "",
        start_date=_parse_date(info.get("start_date") or meta["start_date"]),
        provenance=info.get("provenance"),
        scenario=info.get("scenario"),
    )
    session.add(world)
    session.flush()

    season_sum = sum(SEASON_FACTOR)
    for item in data["regions"]:
        population = int(item.get("population") or 0)
        annual_need = population * GRAIN_PER_CAPITA_MONTH * 12
        session.add(
            Region(
                world_id=world.id,
                key=item["key"],
                name=item["name"],
                level=item.get("level", "region"),
                parent_key=item.get("parent_key"),
                lat=float(item.get("lat") or 0.0),
                lon=float(item.get("lon") or 0.0),
                geo=item.get("geo"),
                population=population,
                farmland=item.get("farmland")
                or round(annual_need * 1.02 / season_sum, 1),
                grain_stock=item.get("grain_stock")
                or round(population * GRAIN_PER_CAPITA_MONTH * 4, 1),
                tax_rate=float(item.get("tax_rate") or 0.08),
                price_index=float(item.get("price_index") or 1.0),
                unrest=float(item.get("unrest") or 0.0),
                faction_key=item.get("faction_key"),
                data_quality=item.get("data_quality")
                or {"population": "estimate", "grain_stock": "assumption"},
            )
        )

    for item in data.get("factions") or []:
        session.add(Faction(world_id=world.id, **item))
    for item in data.get("relations") or []:
        session.add(FactionRelation(world_id=world.id, **item))

    for pair in data.get("trade_pairs") or []:
        a_key, b_key, capacity = pair[0], pair[1], pair[2]
        for src, dst in ((a_key, b_key), (b_key, a_key)):
            session.add(
                TradeLink(
                    world_id=world.id,
                    from_key=src,
                    to_key=dst,
                    kind="grain",
                    capacity=capacity,
                    loss=0.08,
                )
            )

    for item in data.get("characters") or []:
        payload = dict(item)
        payload.setdefault("office_rank", 5)
        payload.setdefault("office_kind", "civil")
        session.add(Character(world_id=world.id, **payload))

    for item in data.get("armies") or []:
        session.add(Army(world_id=world.id, **item))

    for item in data.get("events") or []:
        payload = dict(item)
        payload["date"] = _parse_date(payload["date"])
        session.add(HistoricalEvent(world_id=world.id, **payload))

    session.flush()
    return world


__all__ = ["ContentError", "content_path", "has_content", "load_content", "seed_content"]