"""世界包：读取、校验、以及「改数据就能改结果」。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import worldpack
from app.engine import ending_rubric
from app.worldpack import PackError, load_rubric, load_world


def test_tang_pack_loads():
    world = load_world("tang_755")
    rubric = load_rubric("tang_755")
    assert world["title"] == "安史之乱"
    assert world["horizon_months"] == rubric["horizon_months"] == 96
    assert world["role"] == "唐廷"
    labels = {m["label"] for m in rubric["metrics"]}
    assert labels == {"两京", "乱平", "府库", "威望", "民心", "河北"}
    assert abs(sum(m["weight"] for m in rubric["metrics"]) - 1.0) < 1e-9


def _write_pack(root: Path, key: str, rubric: dict) -> None:
    d = root / key
    d.mkdir(parents=True)
    (d / "world.json").write_text(
        json.dumps(
            {
                "key": key,
                "title": "测试世界",
                "start_date": "0200-01-01",
                "horizon_months": 12,
                "role": "测试者",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (d / "rubric.json").write_text(json.dumps(rubric, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def pack_root(tmp_path, monkeypatch):
    monkeypatch.setattr(worldpack, "PACK_ROOT", tmp_path)
    worldpack._load_pack_cached.cache_clear()
    return tmp_path


def _minimal_rubric(**overrides):
    rubric = {
        "horizon_months": 12,
        "grades": [[50, "及格", "还行"], [0, "不及格", "不行"]],
        "metrics": [
            {"key": "a", "label": "甲", "weight": 1.0, "kind": "max_unrest", "span": 1.0}
        ],
    }
    rubric.update(overrides)
    return rubric


def test_minimal_pack_is_accepted(pack_root):
    _write_pack(pack_root, "demo", _minimal_rubric())
    assert load_rubric("demo")["metrics"][0]["label"] == "甲"


def test_weights_must_sum_to_one(pack_root):
    bad = _minimal_rubric()
    bad["metrics"][0]["weight"] = 0.7
    _write_pack(pack_root, "demo", bad)
    with pytest.raises(PackError, match="权重之和"):
        load_rubric("demo")


def test_unknown_metric_kind_is_reported(pack_root):
    bad = _minimal_rubric()
    bad["metrics"][0]["kind"] = "magic"
    _write_pack(pack_root, "demo", bad)
    with pytest.raises(PackError, match="kind='magic'"):
        load_rubric("demo")


def test_missing_field_points_at_the_field(pack_root):
    bad = _minimal_rubric()
    del bad["metrics"][0]["weight"]
    _write_pack(pack_root, "demo", bad)
    with pytest.raises(PackError, match="weight"):
        load_rubric("demo")


def test_world_key_must_match_directory(pack_root):
    _write_pack(pack_root, "demo", _minimal_rubric())
    path = pack_root / "demo" / "world.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["key"] = "other"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PackError, match="不一致"):
        load_world("demo")


# ---------------- 指标计算：纯函数，不碰数据库 ----------------
def _ctx(regions=(), factions=None, characters=None):
    region_list = list(regions)
    return SimpleNamespace(
        regions=region_list,
        by_key={r.region_key: r for r in region_list},
        factions=factions or {},
        characters=characters or {},
        regions_of=lambda keys: [next((r for r in region_list if r.region_key == k), None) for k in keys],
    )


def _region(key, faction="tang", autonomous=False, unrest=0.0, name=None):
    return SimpleNamespace(
        region_key=key, faction_key=faction, autonomous=autonomous, unrest=unrest,
        name=name or key,
    )


def test_regions_held_counts_only_target_faction():
    ctx = _ctx([_region("a"), _region("b", faction="rebel"), _region("c")])
    score = ending_rubric._regions_held(
        ctx, {"regions": ["a", "b", "c"], "faction": "tang"}
    )
    assert score == pytest.approx(2 / 3 * 100)


def test_region_control_three_tiers():
    spec = {"regions": ["h1", "h2"], "faction": "tang",
            "scores": {"held": 100, "autonomous": 40, "lost": 0}}
    held = _ctx([_region("h1"), _region("h2")])
    auto = _ctx([_region("h1"), _region("h2", autonomous=True)])
    lost = _ctx([_region("h1"), _region("h2", faction="rebel")])
    assert ending_rubric._region_control(held, spec) == 100
    assert ending_rubric._region_control(auto, spec) == 40
    assert ending_rubric._region_control(lost, spec) == 0


def test_rebellion_status_three_tiers():
    spec = {"faction": "tang", "leader": "boss",
            "scores": {"over": 100, "leader_gone": 60, "ongoing": 20}}
    alive = _ctx([_region("a")], characters={"boss": SimpleNamespace(alive=True)})
    dead = _ctx([_region("a", faction="rebel")],
                characters={"boss": SimpleNamespace(alive=False)})
    ongoing = _ctx([_region("a", faction="rebel")],
                   characters={"boss": SimpleNamespace(alive=True)})
    assert ending_rubric._rebellion_status(alive, spec) == 100
    assert ending_rubric._rebellion_status(dead, spec) == 60
    assert ending_rubric._rebellion_status(ongoing, spec) == 20


def test_max_unrest_is_inverted():
    ctx = _ctx([_region("a", unrest=0.35)])
    assert ending_rubric._max_unrest(ctx, {"span": 0.7, "invert": True}) == pytest.approx(50.0)


def test_treasury_scaling_is_clamped():
    fac = SimpleNamespace(treasury=-500_000)
    ctx = _ctx(factions={"tang": fac})
    spec = {"faction": "tang", "offset": 500_000, "span": 8_000_000}
    assert ending_rubric._faction_treasury(ctx, spec) == 0
    fac.treasury = 7_500_000
    assert ending_rubric._faction_treasury(ctx, spec) == 100