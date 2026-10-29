"""规则里的算术表达式：让 JSON 能表达「按府库透支额算军饷」这类动态数值。

只允许一小撮语法，不是 eval：

    数字、变量名、+ - * /、括号、min() max() abs() round()

变量来自引擎给的环境（月数、聚光地区的人口与民变、各派系的府库/威望/兵力）。
写错会报错并指出表达式与未知变量，不用等到运行时才炸。
"""

from __future__ import annotations

import ast
from typing import Any

from ..worldpack import PackError

#: 允许的函数
FUNCS = {"min": min, "max": max, "abs": abs, "round": round}

#: 允许的二元运算
BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _eval(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise PackError(f"表达式里只能出现数字，出现了 {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise PackError(
                f"表达式引用了未知变量 '{node.id}'。可用变量：{', '.join(sorted(env))}"
            )
        return float(env[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, env)
    if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
        return BINOPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise PackError(
                f"表达式里只允许 {', '.join(sorted(FUNCS))}，出现了其他函数"
            )
        args = [_eval(a, env) for a in node.args]
        return float(FUNCS[node.func.id](*args))
    raise PackError(f"表达式里有不支持的语法：{type(node).__name__}")


def evaluate(expr: str, env: dict[str, float]) -> float:
    """算一个表达式。出错时消息里带上原表达式。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise PackError(f"表达式语法错误：{expr!r}（{exc.msg}）") from exc
    try:
        return _eval(tree, env)
    except PackError as exc:
        raise PackError(f"{exc}（表达式：{expr!r}）") from exc


def is_expression(value: Any) -> bool:
    """字符串视为表达式；{"expr": "..."} 也可以。"""
    return isinstance(value, str) or (
        isinstance(value, dict) and isinstance(value.get("expr"), str)
    )


def expression_of(value: Any) -> str:
    return value if isinstance(value, str) else value["expr"]


def build_env(
    *,
    month: int,
    regions_by_key: dict[str, Any],
    focus_region: str | None,
    factions: dict[str, Any],
) -> dict[str, float]:
    """给表达式用的变量环境。

    命名约定（简单、可猜）：
      month                当前推进月数
      focus_population     聚光地区人口
      focus_unrest         聚光地区民变
      f_<派系>_treasury    该派系府库
      f_<派系>_legitimacy  该派系威望
      f_<派系>_military    该派系兵力
    """
    env: dict[str, float] = {"month": float(month)}
    focus = regions_by_key.get(focus_region) if focus_region else None
    if focus is not None:
        env["focus_population"] = float(getattr(focus, "population", 0) or 0)
        env["focus_unrest"] = float(getattr(focus, "unrest", 0) or 0)
        env["focus_price"] = float(getattr(focus, "price_index", 1) or 1)
    for key, fac in factions.items():
        env[f"f_{key}_treasury"] = float(getattr(fac, "treasury", 0) or 0)
        env[f"f_{key}_legitimacy"] = float(getattr(fac, "legitimacy", 0) or 0)
        env[f"f_{key}_military"] = float(getattr(fac, "military", 0) or 0)
    return env


__all__ = [
    "FUNCS",
    "build_env",
    "evaluate",
    "expression_of",
    "is_expression",
]