"""A second deterministic detector, read out of real Python source exactly
the way causeway.analysis.detectors's SQL-predicate one is: a linter with a
counterfactual attached, never a decision about which hypothesis is causal.

The pattern: a function that acquires something (`X.acquire()`) and later,
as a SIBLING statement in the same block - not inside a try/finally -
releases it (`X.release()`). Every exception raised by anything in between
skips the release, which is exactly what "a connection pool is not giving
connections back" looks like from source: nothing here claims to know that
IS what is happening at runtime, only that this shape, if it runs into an
exception on the acquired resource's watch, leaks it. The counterfactual
this detector derives - wrapping the same statements in `try/finally` - is
mechanical: the release call moves into a finally block, verbatim, so it
runs whether or not anything above it raised.
"""
from __future__ import annotations

import ast
import re
from typing import List, Optional, Sequence

from causeway.analysis.excerpt import excerpt_for
from causeway.analysis.hypothesis import CodeHypothesis

NAME = "resource_release_not_guaranteed"

_LEADING_WS = re.compile(r"^[ \t]*")


def _call_of(stmt: ast.stmt) -> Optional[ast.Call]:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return stmt.value
    return None


def _attr_call_owner(call: ast.Call, method: str) -> Optional[str]:
    """If `call` is `<owner-expression>.<method>(...)`, the source text of
    the owner expression (so `pool.acquire()` and `pool.release()` can be
    matched as the same owner, and `self._pool.acquire()` is not confused
    with an unrelated `other.acquire()`). None if `call` is not that shape."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == method):
        return None
    return ast.dump(func.value)   # a structural key, not source text - fine for equality


def _reindent(line: str, indent: str) -> str:
    if not line.strip():
        return line
    return indent + line.lstrip()


def _find_pair(body: Sequence[ast.stmt]):
    """The first (acquire_index, release_index) pair in `body` where
    everything between them is ordinary statements - no Try, meaning
    nothing already guarantees the release runs. None if there is no such
    pair."""
    acquire_index = None
    owner = None
    for index, stmt in enumerate(body):
        call = _call_of(stmt)
        if call is None:
            continue
        if acquire_index is None:
            acquired_owner = _attr_call_owner(call, "acquire")
            if acquired_owner is not None:
                acquire_index, owner = index, acquired_owner
            continue
        released_owner = _attr_call_owner(call, "release")
        if released_owner is not None and released_owner == owner:
            between = body[acquire_index + 1:index]
            if index > acquire_index + 1 and not any(
                    isinstance(s, (ast.Try, ast.With, ast.AsyncWith)) for s in between):
                return acquire_index, index
            # A release right after an already-safe span (or immediately
            # after acquire, with nothing to leak) - not a finding either
            # way; keep looking in case a later pair in this same body is.
            acquire_index, owner = None, None
    return None


def _derive_finding(relative_path: str, source: str, source_lines: Sequence[str],
                    func: ast.FunctionDef, acquire_stmt: ast.stmt, release_stmt: ast.stmt
                    ) -> CodeHypothesis:
    start_line, end_line = acquire_stmt.lineno, release_stmt.end_lineno
    observed_lines = source_lines[start_line - 1:end_line]
    observed = "".join(observed_lines)

    indent = _LEADING_WS.match(observed_lines[0]).group(0)
    inner_indent = indent + "    "
    acquire_line = observed_lines[0]
    release_line = observed_lines[-1]
    between_lines = observed_lines[1:-1]

    rebuilt_body = "".join(_reindent(line, inner_indent) for line in between_lines)
    counterfactual = (
        acquire_line
        + indent + "try:\n"
        + rebuilt_body
        + indent + "finally:\n"
        + inner_indent + release_line.strip() + "\n"
    )

    return CodeHypothesis(
        file=relative_path, line=start_line, symbol=func.name,
        kind="resource_release", observed=observed, counterfactual=counterfactual,
        evidence=observed,
        reason=("this function acquires a resource and releases it as a later, "
               "ordinary statement rather than inside a try/finally - any "
               "exception raised in between skips the release, which is what a "
               "connection pool or lock that slowly runs out of capacity under "
               "load looks like from the source"),
        detector=NAME, context=("symbol %s" % func.name,),
        excerpt=excerpt_for(source, start_line, end_line),
    )


def scan_source(relative_path: str, source: str) -> List[CodeHypothesis]:
    findings: List[CodeHypothesis] = []
    lines = source.splitlines(keepends=True)
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        pair = _find_pair(node.body)
        if pair is None:
            continue
        acquire_index, release_index = pair
        findings.append(_derive_finding(
            relative_path, source, lines, node, node.body[acquire_index], node.body[release_index]))
    return findings


def scan_repository(workspace: str, roots: Sequence[str] = ()) -> List[CodeHypothesis]:
    import os

    findings: List[CodeHypothesis] = []
    for relative in sorted(roots):
        if not relative.endswith(".py"):
            continue
        path = os.path.join(workspace, relative)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        try:
            findings.extend(scan_source(relative, source))
        except SyntaxError:
            continue
    findings.sort(key=lambda h: (h.file, h.line, h.symbol))
    return findings
