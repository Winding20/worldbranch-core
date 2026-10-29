"""世界包（world pack）：把一段历史做成**数据**，引擎只负责解释。

目录约定（相对 backend/app/seed/packs/）：

    <world_key>/
      world.json      元信息：标题、时段、角色、期限、文案
      rubric.json     结局评分：指标、权重、等级
      decisions.json  决策规则（后续步骤接入）
      geo.json        地理边界（可选，也可复用 seed/data 下的文件）

设计原则：
  - 引擎不认识任何具体地名、人名、派系名；
  - 所有「哪个地区 / 哪个派系」都由包里写清楚；
  - 包缺失或字段不合法时，报出**能定位到文件与字段**的错误，而不是静默降级。

本模块只负责「找到包、读出来、做基础校验」，不含业务逻辑。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

#: 世界包根目录
PACK_ROOT = Path(__file__).resolve().parent / "seed" / "packs"


class PackError(ValueError):
    """世界包不合法。消息里必须带文件与字段，方便作者定位。"""


def pack_dir(world_key: str) -> Path:
    return PACK_ROOT / world_key


def pack_exists(world_key: str) -> bool:
    return (pack_dir(world_key) / "world.json").is_file()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PackError(f"缺少文件：{path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackError(f"读不了 {path}：{exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PackError(f"{path.name} 不是合法 JSON（第 {exc.lineno} 行）：{exc.msg}") from exc
    if not isinstance(data, dict):
        raise PackError(f"{path.name} 顶层必须是对象")
    return data


def load_world(world_key: str) -> dict[str, Any]:
    """读 world.json 并做最小校验。"""
    data = _read_json(pack_dir(world_key) / "world.json")
    for field in ("key", "title", "start_date", "horizon_months", "role"):
        if not data.get(field):
            raise PackError(f"world.json 缺少必填字段：{field}")
    if data["key"] != world_key:
        raise PackError(f"world.json 的 key（{data['key']}）与目录名（{world_key}）不一致")
    return data


def _validate_metric(spec: dict[str, Any], where: str) -> None:
    for field in ("key", "label", "weight", "kind"):
        if field not in spec:
            raise PackError(f"{where} 缺少字段：{field}")
    if spec["kind"] not in KNOWN_METRIC_KINDS:
        raise PackError(
            f"{where} 的 kind='{spec['kind']}' 不认识。"
            f"可用：{', '.join(sorted(KNOWN_METRIC_KINDS))}"
        )
    weight = spec["weight"]
    if not isinstance(weight, (int, float)) or weight < 0:
        raise PackError(f"{where} 的 weight 必须是非负数")


#: 结局指标的计算方式。新增一种只需在这里登记并在 ending_rubric 里实现。
KNOWN_METRIC_KINDS = {
    "regions_held",        # 指定地区中有几个在目标派系手里
    "rebellion_status",    # 叛乱是否平定（含“首领已死”这种半平定）
    "faction_treasury",    # 派系府库，按偏移与跨度折算
    "faction_field",       # 派系某个 0–1 字段（如威望）
    "max_unrest",          # 全境最高民变，越高越差（反向）
    "region_control",      # 地区实际控制权：直辖 / 名义自专 / 沦陷
    "unrest_pressure",     # 民变：平均与峰值加权（越高越差）
    "avg_price",           # 平均粮价指数（越高越差）
    "relation_value",      # 与某派系的关系值
}


def load_rubric(world_key: str) -> dict[str, Any]:
    """读 rubric.json：结局指标、权重、等级、事实清单。"""
    data = _read_json(pack_dir(world_key) / "rubric.json")

    horizon = data.get("horizon_months")
    if not isinstance(horizon, int) or horizon <= 0:
        raise PackError("rubric.json 的 horizon_months 必须是正整数")

    metrics = data.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise PackError("rubric.json 的 metrics 必须是非空数组")
    seen: set[str] = set()
    for i, spec in enumerate(metrics):
        if not isinstance(spec, dict):
            raise PackError(f"metrics[{i}] 必须是对象")
        _validate_metric(spec, f"metrics[{i}]")
        if spec["label"] in seen:
            raise PackError(f"metrics[{i}] 的 label 重复：{spec['label']}")
        seen.add(spec["label"])

    total = sum(m["weight"] for m in metrics)
    if abs(total - 1.0) > 1e-6:
        raise PackError(f"metrics 的权重之和应为 1.0，实际 {total:.3f}")

    grades = data.get("grades")
    if not isinstance(grades, list) or not grades:
        raise PackError("rubric.json 的 grades 必须是非空数组")
    for i, g in enumerate(grades):
        if not (isinstance(g, list) and len(g) == 3):
            raise PackError(f"grades[{i}] 必须是 [门槛, 名称, 评语] 三元组")
        if not isinstance(g[0], (int, float)):
            raise PackError(f"grades[{i}] 的门槛必须是数字")
    return data


def load_decisions(world_key: str) -> dict[str, Any]:
    """读决策规则。

    两个来源，按顺序合并：
      1. PACK_ROOT/_common/decisions.json —— 所有世界通用（如赈济、漕运）
      2. <world_key>/decisions.json —— 本世界专属，同名 kind 覆盖通用规则
    """
    rules: list[dict[str, Any]] = []
    common = PACK_ROOT / "_common" / "decisions.json"
    if common.is_file():
        rules.extend(_read_json(common).get("rules") or [])

    path = pack_dir(world_key) / "decisions.json"
    if path.is_file():
        data = _read_json(path)
        # include：引用共享规则文件（相对 PACK_ROOT），避免多个世界复制同一份
        for rel in data.get("include") or []:
            inc = PACK_ROOT / rel
            if not inc.is_file():
                raise PackError(f"include 的文件不存在：{rel}")
            rules.extend(_read_json(inc).get("rules") or [])
        own = data.get("rules") or []
        own_kinds = {r.get("kind") for r in own}
        rules = [r for r in rules if r.get("kind") not in own_kinds]
        rules.extend(own)
        data = {"rules": rules}
    else:
        data = {"rules": rules}

    data = {"rules": rules}
    if not isinstance(rules, list):
        raise PackError("decisions.json 的 rules 必须是数组")

    # 基础校验：条件与效果的名字必须在词表里（值本身由引擎在运行时判定）
    from .engine.rules_engine import CONDITIONS, EFFECTS

    for i, rule in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(rule, dict):
            raise PackError(f"{where} 必须是对象")
        if not rule.get("kind"):
            raise PackError(f"{where} 缺少字段：kind")
        if not (rule.get("options") or rule.get("options_from")):
            raise PackError(f"{where} 需要 options 或 options_from 之一")
        if rule.get("options_from"):
            from .engine.rule_generators import GENERATORS

            if rule["options_from"] not in GENERATORS:
                raise PackError(
                    f"{where} 的 options_from '{rule['options_from']}' 不存在。"
                    f"可用：{', '.join(sorted(GENERATORS))}"
                )
        for name in (rule.get("when") or {}):
            if name not in CONDITIONS:
                raise PackError(
                    f"{where} 的条件 '{name}' 不认识。"
                    f"可用：{', '.join(sorted(CONDITIONS))}"
                )
        for j, opt in enumerate(rule.get("options") or []):
            if not opt.get("label"):
                raise PackError(f"{where}.options[{j}] 缺少 label")
            for name in (opt.get("effects") or {}):
                if name not in EFFECTS:
                    raise PackError(
                        f"{where}.options[{j}] 的效果 '{name}' 不认识。"
                        f"可用：{', '.join(sorted(EFFECTS))}"
                    )
    return data


@lru_cache(maxsize=32)
def _load_pack_cached(world_key: str, _mtime: float) -> dict[str, Any]:
    return {
        "world": load_world(world_key),
        "rubric": load_rubric(world_key),
    }


def load_pack(world_key: str) -> dict[str, Any]:
    """读整个包（元信息 + 结局规则）。

    用 world.json 的修改时间做缓存键：开发时改了文件会立刻生效。
    """
    path = pack_dir(world_key) / "world.json"
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    return _load_pack_cached(world_key, mtime)


__all__ = [
    "PACK_ROOT",
    "PackError",
    "KNOWN_METRIC_KINDS",
    "load_pack",
    "load_decisions",
    "load_rubric",
    "load_world",
    "pack_dir",
    "pack_exists",
]