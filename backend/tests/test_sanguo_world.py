"""第二个世界：赤壁。

这个文件的意义不在"多测几个断言"，而在证明——
**一段全新的历史可以只靠 JSON 数据就跑起来**，引擎不需要为它改一行代码。
"""

from __future__ import annotations

from sqlalchemy import select

from app.engine import SimulationEngine
from app.engine.decisions import pending_decisions
from app.engine.ending_rubric import evaluate_rubric
from app.models import ArmyState, CharacterState, Decision, HistoricalFire, Region
from app.services import create_branch, get_world_by_key
from app.worldpack import load_decisions, load_rubric, load_world


def test_sanguo_seeded_from_json(db_session):
    """世界、地区、人物、军队、史实全部来自 content.json。"""
    world = get_world_by_key(db_session, "sanguo_208")
    assert world.name == "赤壁"
    assert world.start_date.isoformat() == "0208-07-01"

    regions = {
        r.key
        for r in db_session.scalars(select(Region).where(Region.world_id == world.id))
    }
    assert regions == {"jiangxia", "jiangling", "chaisang", "xiangyang"}

    chars = {
        c.key
        for c in db_session.scalars(
            select(CharacterState).where(CharacterState.branch_id == -1)
        )
    }
    assert chars == set()  # 角色状态在分支里，见下一个测试


def test_sanguo_branch_has_characters_and_armies(db_session):
    world = get_world_by_key(db_session, "sanguo_208")
    branch = create_branch(db_session, world, name="赤壁测试", seed=7)

    chars = {
        c.character_key
        for c in db_session.scalars(
            select(CharacterState).where(CharacterState.branch_id == branch.id)
        )
    }
    assert {"liu_bei", "zhuge_liang", "zhou_yu", "cao_cao"} <= chars

    armies = {
        a.army_key
        for a in db_session.scalars(
            select(ArmyState).where(ArmyState.branch_id == branch.id)
        )
    }
    assert {"liu_army", "wu_army", "cao_army"} <= armies


def test_sanguo_pack_loads(db_session):
    meta = load_world("sanguo_208")
    rubric = load_rubric("sanguo_208")
    rules = load_decisions("sanguo_208")

    assert meta["player_faction"] == "liu"
    assert meta["hostile_faction"] == "cao"
    assert rubric["horizon_months"] == 12
    labels = {m["label"] for m in rubric["metrics"]}
    assert labels == {"立足", "联盟", "兵力"}
    assert {r["kind"] for r in rules["rules"]} == {"alliance_choice", "seize_jiangling"}


def test_sanguo_events_fire_on_schedule(db_session):
    world = get_world_by_key(db_session, "sanguo_208")
    branch = create_branch(db_session, world, name="史实测试", seed=3)
    SimulationEngine(db_session).advance(branch, months=5)

    fired = {
        row.event_key
        for row in db_session.scalars(
            select(HistoricalFire).where(HistoricalFire.branch_id == branch.id)
        )
    }
    assert "cao_takes_jiangling" in fired
    assert "alliance_formed" in fired


def test_sanguo_decision_comes_from_json(db_session):
    """「鲁肃来见」这条规则只存在于 decisions.json。"""
    world = get_world_by_key(db_session, "sanguo_208")
    branch = create_branch(db_session, world, name="决策测试", seed=5)
    SimulationEngine(db_session).advance(branch, months=1)

    kinds = {d.kind for d in pending_decisions(db_session, branch.id)}
    assert "alliance_choice" in kinds

    made = db_session.scalar(
        select(Decision).where(
            Decision.branch_id == branch.id, Decision.kind == "alliance_choice"
        )
    )
    assert made.title == "鲁肃来见"
    assert {o["key"] for o in made.options} == {"ally", "go_west"}


def test_sanguo_ending_uses_its_own_rubric(db_session):
    world = get_world_by_key(db_session, "sanguo_208")
    branch = create_branch(db_session, world, name="结局测试", seed=9)
    SimulationEngine(db_session).advance(branch, months=12)

    rubric = load_rubric("sanguo_208")
    ending = evaluate_rubric(db_session, branch, rubric)
    assert ending is not None
    assert set(ending["scores"]) == {"立足", "联盟", "兵力"}
    assert ending["grade"] in {"鼎足", "存立", "寄人篱下", "覆没"}
    assert 0 <= ending["score"] <= 100


def test_tang_and_sanguo_use_different_player_factions(db_session):
    """同一套引擎，玩家派系由世界包决定。"""
    assert load_world("tang_755")["player_faction"] == "tang"
    assert load_world("sanguo_208")["player_faction"] == "liu"