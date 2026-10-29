"""数据驱动的决策：条件、效果、以及「只写 JSON 就能产出决策」。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import worldpack
from app.engine import rules_engine, simulation
from app.engine.decisions import pending_decisions
from app.engine.rules_engine import State, apply_effects, conditions_met
from app.engine.simulation import SimulationEngine
from app.models import Decision
from app.services import create_branch, get_world_by_key


# ---------------- 状态与条件 ----------------
def _state(unrest=0.0, treasury=0.0, legitimacy=1.0, regions=None):
    regions = regions or [
        SimpleNamespace(region_key="a", name="甲地", faction_key="me", unrest=unrest,
                        autonomous=False, grain_stock=1000),
        SimpleNamespace(region_key="b", name="乙地", faction_key="other", unrest=0.0,
                        autonomous=False, grain_stock=1000),
    ]
    return State(
        regions=regions,
        factions={"me": SimpleNamespace(treasury=treasury, legitimacy=legitimacy)},
    )


def test_empty_conditions_always_met():
    assert conditions_met(_state(), {})


def test_unrest_and_treasury_conditions():
    s = _state(unrest=0.8, treasury=-5)
    assert conditions_met(s, {"unrest_above": 0.5})
    assert not conditions_met(s, {"unrest_above": 0.9})
    assert conditions_met(s, {"treasury_below": 0})
    assert not conditions_met(s, {"treasury_below": -100})


def test_region_held_by():
    s = _state()
    assert conditions_met(s, {"region_held_by": {"region": "a", "faction": "me"}})
    assert not conditions_met(s, {"region_held_by": {"region": "b", "faction": "me"}})


def test_all_conditions_must_hold():
    s = _state(unrest=0.8, treasury=10)
    assert not conditions_met(s, {"unrest_above": 0.5, "treasury_below": 0})


def test_unknown_condition_is_reported():
    with pytest.raises(worldpack.PackError, match="未知的条件"):
        conditions_met(_state(), {"moon_phase": "full"})


# ---------------- 效果 ----------------
def _ctx():
    region = SimpleNamespace(region_key="a", unrest=0.2, grain_stock=100,
                             autonomous=False, faction_key="me")
    return {
        "player_faction": SimpleNamespace(treasury=0.0, legitimacy=0.5),
        "regions_by_key": {"a": region},
        "characters": {"p": SimpleNamespace(loyalty=0.6)},
        "armies": {"x": SimpleNamespace(troops=100, morale=0.5)},
        "relations": {"r": SimpleNamespace(value=0.3)},
        "focus_region": "a",
    }


def test_effects_apply_to_numbers():
    ctx = _ctx()
    apply_effects(
        ctx,
        {
            "treasury": -50,
            "legitimacy": 0.2,
            "unrest": {"target": "focus", "delta": -0.1},
            "grain": {"a": 200},
            "loyalty": {"p": -0.1},
            "troops": {"x": -20},
            "morale": {"x": 0.1},
            "relation": {"r": -0.2},
            "autonomy": {"a": True},
        },
    )
    assert ctx["player_faction"].treasury == -50
    assert ctx["player_faction"].legitimacy == pytest.approx(0.7)
    assert ctx["regions_by_key"]["a"].unrest == pytest.approx(0.1)
    assert ctx["regions_by_key"]["a"].grain_stock == 300
    assert ctx["characters"]["p"].loyalty == pytest.approx(0.5)
    assert ctx["armies"]["x"].troops == 80
    assert ctx["armies"]["x"].morale == pytest.approx(0.6)
    assert ctx["relations"]["r"].value == pytest.approx(0.1)
    assert ctx["regions_by_key"]["a"].autonomous is True


def test_unknown_effect_is_reported():
    with pytest.raises(worldpack.PackError, match="未知的效果"):
        apply_effects(_ctx(), {"summon_dragon": True})


# ---------------- 端到端：只写 JSON 就能产出决策 ----------------
@pytest.fixture()
def pack_with_rule(tmp_path, monkeypatch):
    """给 tang_755 挂一个临时世界包，里面只有一条必然触发的规则。"""
    pack_dir = tmp_path / "tang_755"
    pack_dir.mkdir()
    (pack_dir / "world.json").write_text(
        json.dumps(
            {
                "key": "tang_755",
                "title": "安史之乱",
                "start_date": "0755-11-01",
                "horizon_months": 96,
                "role": "唐廷",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (pack_dir / "decisions.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "kind": "pack_smoke",
                        "title": "{region}出现异动",
                        "description": "这是一条由数据产生的决策。",
                        "focus": {"strategy": "worst_unrest"},
                        "when": {},
                        "options": [
                            {
                                "key": "act",
                                "label": "处置",
                                "note": "府库 −1000",
                                "effects": {"treasury": -1000},
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worldpack, "PACK_ROOT", tmp_path)
    monkeypatch.setattr(simulation, "pack_exists", lambda key: True)
    worldpack._load_pack_cached.cache_clear()
    return pack_dir


def test_pack_rule_creates_decision(db_session, pack_with_rule):
    world = get_world_by_key(db_session, "tang_755")
    branch = create_branch(db_session, world, name="规则测试", seed=11)
    SimulationEngine(db_session).advance(branch, months=1)

    rows = list(
        db_session.scalars(
            select(Decision).where(
                Decision.branch_id == branch.id, Decision.kind == "pack_smoke"
            )
        )
    )
    assert rows, "世界里应出现由 decisions.json 产生的决策"
    decision = rows[0]
    assert decision.title.endswith("出现异动")
    assert decision.focus_region, "应按 strategy=worst_unrest 聚光到某个地区"
    assert decision.options[0]["effects"] == {"treasury": -1000}


def test_pack_rule_bad_condition_rejected_by_loader(tmp_path, monkeypatch):
    d = tmp_path / "bad_000"
    d.mkdir()
    (d / "world.json").write_text(
        json.dumps(
            {
                "key": "bad_000",
                "title": "坏包",
                "start_date": "0200-01-01",
                "horizon_months": 12,
                "role": "测试",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (d / "decisions.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "kind": "x",
                        "when": {"no_such_condition": 1},
                        "options": [{"label": "a"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worldpack, "PACK_ROOT", tmp_path)
    with pytest.raises(worldpack.PackError, match="no_such_condition"):
        worldpack.load_decisions("bad_000")