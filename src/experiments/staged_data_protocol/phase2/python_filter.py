"""Finite filter expressions parsed as AST; never execute model-authored code.

The parsed tree is shared by validation, reference binding and providers. SQL
identifiers come only from provider mappings; values are always parameters.
Legacy DSL parsing remains at its existing compatibility boundary.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from functools import lru_cache
from typing import Any, Callable, Mapping


class FilterSyntaxError(ValueError):
    pass


def _field(node: ast.AST) -> str:
    if isinstance(node, ast.Name) and not node.id.startswith("_"):
        return node.id
    raise FilterSyntaxError("filter fields must be current dataview field names")


def _value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _value(node.operand)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_value(item) for item in node.elts]
        if all(not isinstance(item, (dict, list)) for item in values):
            return values
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        name = node.value.id
        if ((name.startswith("r") and name[1:].isdigit()) or
                (name.startswith("step") and name[4:].isdigit())) and not node.attr.startswith("_"):
            return {"result": name, "field": node.attr}
    raise FilterSyntaxError("filter values must be quoted text, numbers, None, lists or an existing result column")


def _node(node: ast.AST) -> dict[str, Any]:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id.startswith(("r", "step")):
        raise FilterSyntaxError("a result column is a set; use field in rN.column, not a bare filter reference")
    if isinstance(node, ast.BoolOp):
        return {"and" if isinstance(node.op, ast.And) else "or": [_node(item) for item in node.values]}
    if isinstance(node, ast.Call):
        # A single protocol spelling, not general Python method dispatch.
        if not (
            isinstance(node.func, ast.Attribute) and node.func.attr == "contains"
            and isinstance(node.func.value, ast.Name)
            and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
        ):
            raise FilterSyntaxError("text matching uses field.contains('text') with one quoted text argument")
        return {"field": _field(node.func.value), "operator": "contains", "value": node.args[0].value}
    if isinstance(node, ast.Compare):
        if len(node.ops) > 1:
            return {"and": [_node(ast.Compare(left=left, ops=[op], comparators=[right]))
                            for left, op, right in zip([node.left, *node.comparators[:-1]], node.ops, node.comparators)]}
        op, right = node.ops[0], node.comparators[0]
        if isinstance(op, ast.In) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            return {"field": _field(right), "operator": "contains", "value": node.left.value}
        field, value = _field(node.left), _value(right)
        ops = {ast.Eq: "==", ast.NotEq: "!=", ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.In: "in", ast.NotIn: "not in", ast.Is: "==", ast.IsNot: "!="}
        operator = ops.get(type(op))
        if operator is None or (isinstance(op, (ast.Is, ast.IsNot)) and value is not None):
            raise FilterSyntaxError("filter identity comparisons only support None")
        if operator in {"in", "not in"} and not isinstance(value, (list, dict)):
            raise FilterSyntaxError("field in requires a list or an existing result column")
        return {"field": field, "operator": operator, "value": value}
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return {"constant": node.value}
    raise FilterSyntaxError("filter accepts comparisons, and/or, field.contains('text') and membership conditions")


@lru_cache(maxsize=512)
def _parse_cached(text: str) -> dict[str, Any] | None:
    if not text or text.lower() in {"all", "none", "true", "*"}:
        return None
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None  # Existing `=`, infix LIKE and unquoted literals use legacy parsing.
    # Unquoted legacy values (e.g. name == 宁德时代) are not new Python literals.
    try:
        return _node(tree.body)
    except FilterSyntaxError:
        def legacy_value(node):
            return isinstance(node, ast.Name) and not node.id.startswith("_") or isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(v, (ast.Name, ast.Constant)) for v in node.elts)
        if isinstance(tree.body, ast.Compare) and legacy_value(tree.body.comparators[0]):
            return None
        raise


def parse_python_filter(text: str) -> dict[str, Any] | None:
    return deepcopy(_parse_cached(str(text or "").strip()))


def condition(args: Mapping[str, Any]) -> dict[str, Any] | None:
    if "_filter_expression" in args:
        return deepcopy(args["_filter_expression"])
    return parse_python_filter(str(args.get("filter") or ""))


def predicates(tree: Mapping[str, Any] | None):
    if not tree:
        return
    for op in ("and", "or"):
        if op in tree:
            for child in tree[op]:
                yield from predicates(child)
            return
    if "field" in tree:
        yield tree


def project(tree: dict[str, Any] | None, fields: set[str]) -> dict[str, Any] | None:
    """Safe prerequisite pushdown. Never drop one arm of an OR."""
    if not tree:
        return None
    if all(p["field"] in fields for p in predicates(tree)):
        return tree
    if "and" in tree:
        children = [result for child in tree["and"] if (result := project(child, fields)) is not None]
        return {"and": children} if children else None
    return None


def require_separable(tree: dict[str, Any], stages: list[set[str]]) -> None:
    """Independent query stages cannot execute an OR spanning their fields."""
    used = {p["field"] for p in predicates(tree)}
    if any(used <= fields for fields in stages):
        return
    if "and" in tree:
        for child in tree["and"]:
            require_separable(child, stages)
        return
    raise FilterSyntaxError("filter cannot be split across query stages without changing meaning; keep the needed fields in one stage or use separate data queries")


def map_predicates(tree: dict[str, Any], mapper: Callable) -> dict[str, Any]:
    for op in ("and", "or"):
        if op in tree:
            return {op: [map_predicates(child, mapper) for child in tree[op]]}
    return mapper(dict(tree)) if "field" in tree else dict(tree)


def bind_references(tree: dict[str, Any], results: Mapping[str, Any]) -> dict[str, Any]:
    def bind(p):
        ref = p.get("value")
        if not isinstance(ref, dict):
            return p
        if p["operator"] not in {"in", "not in"}:
            raise FilterSyntaxError("result columns are sets; use field in rN.column")
        handle = results.get(ref["result"])
        if handle is None or ref["field"] not in handle.columns:
            raise FilterSyntaxError(f"unknown result column {ref['result']}.{ref['field']}")
        rows = handle.data.get("rows", []) if isinstance(handle.data, Mapping) else []
        p["value"] = list(dict.fromkeys(row.get(ref["field"]) for row in rows if row.get(ref["field"]) is not None))
        return p
    return map_predicates(tree, bind)


def certainly_empty(tree: Mapping[str, Any]) -> bool:
    if "constant" in tree:
        return tree["constant"] is False
    if "and" in tree:
        return any(certainly_empty(child) for child in tree["and"])
    if "or" in tree:
        return all(certainly_empty(child) for child in tree["or"])
    return tree.get("operator") == "in" and tree.get("value") == []


def compile_predicate(p: Mapping[str, Any], fields: Mapping[str, str]) -> tuple[str, list[Any]]:
    field, op, value = p["field"], p["operator"], p["value"]
    if field not in fields:
        raise FilterSyntaxError(f"filter field={field} is not available at this query stage")
    column = fields[field]
    if isinstance(value, dict):
        raise FilterSyntaxError("unbound result column in filter")
    if op == "contains":
        # ! is an explicit SQL escape character, independent of backslash SQL mode.
        pattern = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        return f"{column} LIKE BINARY %s ESCAPE '!'", ["%" + pattern + "%"]
    if op in {"in", "not in"}:
        if not isinstance(value, list):
            raise FilterSyntaxError("membership requires bound list values")
        if not value:
            return ("1=1" if op == "not in" else "0=1"), []
        return f"{column} {op.upper()} ({', '.join(['%s'] * len(value))})", list(value)
    if value is None and op in {"=", "==", "!="}:
        return f"{column} IS {'NOT ' if op == '!=' else ''}NULL", []
    if op not in {"=", "==", "!=", ">", ">=", "<", "<="}:
        raise FilterSyntaxError(f"unsupported filter operator={op}")
    return f"{column} {'=' if op == '==' else op} %s", [value]


def compile_tree(tree: dict[str, Any] | None, fields: Mapping[str, str], leaf: Callable | None = None) -> tuple[str, list[Any]]:
    if tree is None:
        return "", []
    if "constant" in tree:
        return ("1=1" if tree["constant"] else "0=1"), []
    for op in ("and", "or"):
        if op in tree:
            parts = [compile_tree(child, fields, leaf) for child in tree[op]]
            return f" {op.upper()} ".join(f"({sql})" for sql, _ in parts), [v for _, values in parts for v in values]
    return leaf(tree) if leaf else compile_predicate(tree, fields)


def evaluate(tree: dict[str, Any] | None, row: Mapping[str, Any]) -> bool:
    if tree is None:
        return True
    if "constant" in tree:
        return tree["constant"]
    if "and" in tree:
        return all(evaluate(child, row) for child in tree["and"])
    if "or" in tree:
        return any(evaluate(child, row) for child in tree["or"])
    field, op, expected = tree["field"], tree["operator"], tree["value"]
    if field not in row:
        raise FilterSyntaxError(f"filter field={field} missing from computed result")
    actual = row[field]
    if expected is None:
        return actual is None if op in {"=", "=="} else actual is not None if op == "!=" else False
    if actual is None:
        return False
    if op == "contains":
        return isinstance(actual, str) and expected in actual
    if op in {"in", "not in"}:
        return actual in expected if op == "in" else actual not in expected and None not in expected
    try:
        return {"=": lambda: actual == expected, "==": lambda: actual == expected, "!=": lambda: actual != expected,
                ">": lambda: actual > expected, ">=": lambda: actual >= expected, "<": lambda: actual < expected, "<=": lambda: actual <= expected}[op]()
    except TypeError:
        return False


def leaf_items(args: Mapping[str, Any], aliases: Mapping[str, str] | None = None) -> list[tuple]:
    """Metadata only (date/field detection), never used to flatten execution."""
    aliases = aliases or {}
    tree = condition(args)
    connector = "OR" if has_or(tree) else "AND"
    return [(connector, aliases.get(p["field"], p["field"]), p["operator"], p["value"]) for p in predicates(tree)]


def has_or(tree: Mapping[str, Any] | None) -> bool:
    return bool(tree) and ("or" in tree or any(has_or(child) for child in tree.get("and", [])))


def without_filter(args: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in {"filter", "_filter_expression"}}


def has_unresolved_refs(args: Mapping[str, Any]) -> bool:
    import re
    tree = condition(args)
    if tree is not None and any(isinstance(p.get("value"), dict) for p in predicates(tree)):
        return True
    values = without_filter(args).values() if tree is not None else args.values()
    return any(isinstance(value, str) and re.search(r"\br\d+\.", value) for value in values)


def sql_filter(args: Mapping[str, Any], fields: Mapping[str, str], *, aliases: Mapping[str, str] | None = None,
               allowed: set[str] | None = None, leaf: Callable | None = None) -> tuple[str, list[Any]]:
    tree = condition(args)
    if aliases:
        tree = map_predicates(tree, lambda p: {**p, "field": aliases.get(p["field"], p["field"])}) if tree else tree
    if allowed is not None:
        tree = project(tree, allowed)
    return compile_tree(tree, fields, leaf)


def and_sql(left: tuple[str, list[Any]], right: tuple[str, list[Any]]) -> tuple[str, list[Any]]:
    return " AND ".join(f"({sql})" for sql, _ in (left, right) if sql), [*left[1], *right[1]]


def to_source(tree: Mapping[str, Any] | None) -> str:
    """Canonical filter expression for editable catalog examples/flow bindings."""
    if tree is None:
        return "True"
    for op in ("and", "or"):
        if op in tree:
            return f" {op} ".join(f"({to_source(child)})" for child in tree[op])
    if "constant" in tree:
        return repr(tree["constant"])
    field, op, value = tree["field"], tree["operator"], tree["value"]
    literal = f"{value['result']}.{value['field']}" if isinstance(value, dict) else repr(value)
    if op == "contains":
        return f"{field}.contains({literal})"
    if value is None and op in {"=", "==", "!="}:
        return f"{field} is {'not ' if op == '!=' else ''}None"
    return f"{field} {'==' if op == '=' else op} {literal}"
