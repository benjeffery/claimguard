"""Gate expression validation and evaluation."""

from __future__ import annotations

import ast
from functools import lru_cache
from typing import Any


_ALLOWED_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Subscript,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.And,
    ast.Or,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Not,
    ast.UAdd,
    ast.USub,
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
)

_ALLOWED_BOOL_OPS: tuple[type[ast.boolop], ...] = (
    ast.And,
    ast.Or,
)

_ALLOWED_BIN_OPS: tuple[type[ast.operator], ...] = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
)

_ALLOWED_UNARY_OPS: tuple[type[ast.unaryop], ...] = (
    ast.Not,
    ast.UAdd,
    ast.USub,
)

_ALLOWED_CMP_OPS: tuple[type[ast.cmpop], ...] = (
    ast.Eq,
    ast.NotEq,
    ast.Gt,
    ast.GtE,
    ast.Lt,
    ast.LtE,
    ast.In,
    ast.NotIn,
)


class _ExprValidator(ast.NodeVisitor):
    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise ValueError(f"unsupported expression node: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != "interface":
            raise ValueError(f"unsupported name: {node.id}")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if not isinstance(node.op, _ALLOWED_BOOL_OPS):
            raise ValueError(f"unsupported bool op: {type(node.op).__name__}")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, _ALLOWED_BIN_OPS):
            raise ValueError(f"unsupported bin op: {type(node.op).__name__}")
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if not isinstance(node.op, _ALLOWED_UNARY_OPS):
            raise ValueError(f"unsupported unary op: {type(node.op).__name__}")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if not node.ops:
            raise ValueError("compare expression requires operator")
        for op in node.ops:
            if not isinstance(op, _ALLOWED_CMP_OPS):
                raise ValueError(f"unsupported compare op: {type(op).__name__}")
        self.generic_visit(node)


@lru_cache(maxsize=1024)
def compile_gate_expr(expr: str) -> Any:
    text = str(expr).strip()
    if not text:
        raise ValueError("gate expr must be non-empty")
    tree = ast.parse(text, mode="eval")
    _ExprValidator().visit(tree)
    return compile(tree, "<gate-expr>", "eval")


def evaluate_gate(interface_obj: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    name = str(gate.get("name", ""))
    expr = str(gate.get("expr", "")).strip()
    if not name:
        return {
            "name": "",
            "expr": expr,
            "pass": False,
            "reason": "invalid_gate_name",
            "value": None,
        }
    if not expr:
        return {
            "name": name,
            "expr": expr,
            "pass": False,
            "reason": "invalid_gate_expr",
            "value": None,
        }

    try:
        code = compile_gate_expr(expr)
        value = eval(code, {"__builtins__": {}}, {"interface": interface_obj})
    except Exception:
        return {
            "name": name,
            "expr": expr,
            "pass": False,
            "reason": "rule_eval_error",
            "value": None,
        }

    if not isinstance(value, bool):
        return {
            "name": name,
            "expr": expr,
            "pass": False,
            "reason": "non_bool_result",
            "value": value,
        }

    return {
        "name": name,
        "expr": expr,
        "pass": bool(value),
        "reason": "expr_true" if value else "expr_false",
        "value": value,
    }
