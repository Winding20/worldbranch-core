"""内置世界的数据包。"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Faction, Region, World
from ..worldpack import PACK_ROOT
from . import anlushan_755, tang_750
from .seed_content import has_content, seed_content

#: Python 播种器（老世界）。数据化的世界走 seed_content，不在这里登记。
WORLD_SEEDERS = {
    "tang_750": tang_750.seed,
    "tang_755": anlushan_755.seed,
}


def _data_worlds() -> list[str]:
    """带 content.json 的世界包：内容全在数据里，不需要 Python 播种器。"""
    if not PACK_ROOT.is_dir():
        return []
    return sorted(
        p.name
        for p in PACK_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith("_") and has_content(p.name)
    )


def all_world_keys() -> list[str]:
    """所有可播种的世界：Python 播种器 + 数据化世界包。"""
    return sorted(set(WORLD_SEEDERS) | set(_data_worlds()))


def seed_world(session: Session, key: str) -> World:
    """按 key 播种任意世界（幂等）。"""
    exists = session.scalar(select(World).where(World.key == key))
    if exists is not None:
        return exists
    # 有 content.json 就以数据为准（Python 播种器退居兜底）
    if has_content(key):
        return seed_content(session, key)
    if key in WORLD_SEEDERS:
        return WORLD_SEEDERS[key](session)
    raise LookupError(f"没有找到世界的播种方式：{key}")

#: 各世界的知识库资料
KNOWLEDGE_PACKS = {
    "tang_750": tang_750.KNOWLEDGE,
    "tang_755": anlushan_755.KNOWLEDGE,
}


def seed_all(session: Session) -> list[World]:
    """幂等地写入所有内置世界。"""
    created: list[World] = []
    for key in all_world_keys():
        created.append(seed_world(session, key))
    session.flush()
    return created


def seed_tang(session: Session) -> World:
    """单独写入唐朝世界（测试与命令行用）。"""
    exists = session.scalar(select(World).where(World.key == tang_750.WORLD_KEY))
    if exists is not None:
        return exists
    world = tang_750.seed(session)
    session.flush()
    return world


def seed_knowledge(session: Session) -> int:
    """为内置世界写入知识库资料（幂等）。"""
    total = 0
    for key, pack in KNOWLEDGE_PACKS.items():
        world = session.scalar(select(World).where(World.key == key))
        if world is None:
            raise LookupError("请先执行 seed 写入世界")
        total += _seed_pack(session, world, pack)
    session.flush()
    return total


def _seed_pack(session: Session, world: World, pack: list[dict]) -> int:
    from ..ai.knowledge import upsert_doc

    count = 0
    for doc in pack:
        upsert_doc(
            session,
            world,
            title=doc["title"],
            content=doc["content"],
            source=doc["source"],
            reliability=doc["reliability"],
            slug=doc["slug"],
        )
        count += 1
    return count


__all__ = [
    "WORLD_SEEDERS",
    "KNOWLEDGE_PACKS",
    "seed_all",
    "seed_knowledge",
    "seed_tang",
    "tang_750",
    "anlushan_755",
    "Faction",
    "Region",
    "World",
    "dt",
]