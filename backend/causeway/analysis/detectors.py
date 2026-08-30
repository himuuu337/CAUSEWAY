"""Deterministic detectors: hypotheses read out of real repository source.

A detector's job is to find places worth *testing*, never to decide which one
is causal. It is a linter with a counterfactual attached: it says "this looks
like it could stop an index being used, and here is the text that would put
that right", and then the sandbox and the measurements settle it.

That distinction is the whole point of the two findings this detector makes
in the demo repository. Both are the same shape - an indexed column wrapped
in an expression, in a predicate compared against a bound parameter. Static
analysis cannot tell them apart, because statically they are identical. One
is on a table with tens of thousands of rows and is the incident; the other
is on a six-row lookup table and costs nothing. Only the experiment knows.

No manifest tells this module anything. It reads the schema to learn which
columns are indexed, and the source to find where those columns are wrapped.
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, List, Sequence, Set, Tuple

from causeway.analysis.excerpt import excerpt_for
from causeway.analysis.hypothesis import CodeHypothesis, line_end_of

NAME = "sql_predicate_index_usability"

# Columns that carry an index, learned from the repository's own schema.
_CREATE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+\S+\s+ON\s+(?P<table>\w+)\s*\(\s*(?P<cols>[^)]+)\)",
    re.IGNORECASE)
_INLINE_PK = re.compile(r"^\s*(?P<col>\w+)\s+[\w()]+\s+PRIMARY\s+KEY", re.IGNORECASE | re.MULTILINE)
_TABLE_PK = re.compile(r"PRIMARY\s+KEY\s*\(\s*(?P<cols>[^)]+)\)", re.IGNORECASE)

# A predicate whose left-hand side wraps a bare column, compared to a bound
# parameter. Two shapes: a function call around the column, or arithmetic on it.
_WRAPPED = re.compile(
    r"(?P<whole>"
    r"(?:(?P<fn>UPPER|LOWER|TRIM|ABS|ROUND|CAST)\s*\(\s*(?P<fcol>\w+)\s*(?:AS\s+\w+\s*)?\)"
    r"|(?P<acol>\w+)\s*(?P<arith>[+\-*/])\s*(?P<num>\d+))"
    r"\s*(?P<cmp>=|<>|!=|<=|>=|<|>)\s*\?"
    r")", re.IGNORECASE)

_LOOKS_LIKE_SQL = re.compile(r"\bSELECT\b|\bWHERE\b|\bFROM\b", re.IGNORECASE)


def indexed_columns(schema_sql: str) -> Set[str]:
    """Every column the repository's own schema puts an index on."""
    found: Set[str] = set()
    for match in _CREATE_INDEX.finditer(schema_sql):
        for column in match.group("cols").split(","):
            found.add(column.strip().split()[0].strip('"`[]').lower())
    for match in _INLINE_PK.finditer(schema_sql):
        found.add(match.group("col").lower())
    for match in _TABLE_PK.finditer(schema_sql):
        for column in match.group("cols").split(","):
            found.add(column.strip().split()[0].strip('"`[]').lower())
    return found


def _sql_strings(source: str) -> List[Tuple[str, int, str]]:
    """Every SQL-looking string literal, with its line and enclosing symbol."""
    tree = ast.parse(source)
    owner: Dict[int, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                owner.setdefault(id(child), node.name)
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if names:
                for child in ast.walk(node.value):
                    owner.setdefault(id(child), names[0])

    out: List[Tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _LOOKS_LIKE_SQL.search(node.value):
                out.append((node.value, node.lineno, owner.get(id(node), "<module>")))
    return out


def _table_for(sql: str) -> str:
    match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
    return match.group(1) if match else ""


def scan_source(relative_path: str, source: str, indexed: Set[str]) -> List[CodeHypothesis]:
    """Find wrapped-indexed-column predicates in one python source file."""
    findings: List[CodeHypothesis] = []
    for sql, line, symbol in _sql_strings(source):
        table = _table_for(sql)
        for match in _WRAPPED.finditer(sql):
            column = (match.group("fcol") or match.group("acol") or "").lower()
            if column not in indexed:
                continue

            observed = match.group("whole")
            counterfactual = "%s %s ?" % (column, match.group("cmp"))
            wrap = ("the %s() function" % match.group("fn").upper() if match.group("fn")
                    else "arithmetic (%s %s)" % (match.group("arith"), match.group("num")))

            # the line the predicate actually sits on, not just the string's start
            offset = sql[:match.start()].count("\n")
            finding_line = line + offset
            findings.append(CodeHypothesis(
                file=relative_path, line=finding_line, symbol=symbol,
                kind="query_predicate", observed=observed,
                counterfactual=counterfactual,
                evidence=observed,
                reason=("%s is indexed by this repository's schema, but the "
                        "predicate wraps it in %s. A wrapped column cannot be "
                        "matched against an index, so this lookup may be "
                        "scanning %s instead of seeking it."
                        % (column, wrap, table or "the table")),
                detector=NAME,
                context=("table %s" % table if table else "",),
                excerpt=excerpt_for(source, finding_line, line_end_of(finding_line, observed)),
            ))
    return findings


def scan_repository(workspace: str, schema_relative: str,
                    roots: Sequence[str] = ()) -> List[CodeHypothesis]:
    """Read the repository's schema, then its source, and report what looks
    testable. Ordered by file then line so the result is stable across runs."""
    schema_path = os.path.join(workspace, schema_relative)
    with open(schema_path, "r", encoding="utf-8") as handle:
        indexed = indexed_columns(handle.read())

    findings: List[CodeHypothesis] = []
    for relative in sorted(roots):
        path = os.path.join(workspace, relative)
        if not os.path.isfile(path) or not relative.endswith(".py"):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        try:
            findings.extend(scan_source(relative, source, indexed))
        except SyntaxError:
            continue

    findings.sort(key=lambda h: (h.file, h.line, h.symbol))
    return findings
