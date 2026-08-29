"""The repository's own database, built from its own declared contract.

A repository-backed investigation must not borrow Causeway's bundled data.
This module builds the repository's database from two things it ships itself:
a schema file, and a declarative seed specification in its manifest.

Deliberately narrow. There is no command string here, no generator script, no
hook: a seed column is one of a closed set of kinds, and the schema may only
contain the statements listed in _ALLOWED. Anything else is rejected before a
byte is written, because "run whatever the repository says" is exactly the
capability this contract exists to withhold.

Deterministic: same manifest, same bytes, on any machine.
"""
from __future__ import annotations

import os
import random
import re
import sqlite3
import string
from typing import Any, List, Mapping, Sequence

from causeway.repository.errors import RepositoryRejected

# Statement verbs a schema file may use. Nothing that reaches outside the
# database file: no ATTACH, no PRAGMA, no extension loading, no dot-commands.
_ALLOWED = ("create table", "create unique index", "create index", "drop table",
            "drop index", "create view", "drop view")
_FORBIDDEN = re.compile(
    r"\b(attach|detach|pragma|load_extension|readfile|writefile|vacuum\s+into)\b",
    re.IGNORECASE)

SEED_KINDS = ("rowid", "cycle", "choice", "text", "const", "int_range")
MAX_ROWS = 500_000
_RNG_SEED = 20260829


def _reject(reason: str):
    raise RepositoryRejected("database", reason)


_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql))


def check_schema(sql: str) -> List[str]:
    """Split a schema file into statements and prove each one is allowed.

    Comments are stripped first: a schema is allowed to explain itself, and a
    leading comment is not a statement Causeway has to vet.
    """
    sql = strip_comments(sql)
    if _FORBIDDEN.search(sql):
        _reject("the schema uses a statement Causeway will not run "
                "(attach, detach, pragma, or an extension load)")
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if not statements:
        _reject("the schema file contains no statements")
    for statement in statements:
        head = " ".join(statement.lower().split())
        if not any(head.startswith(verb) for verb in _ALLOWED):
            _reject("the schema may only create or drop tables, indexes and "
                    "views - found %r" % statement.split("\n")[0][:60])
    return statements


def _column_value(kind: Mapping[str, Any], row: int, rng: random.Random,
                  alphabet: str) -> Any:
    what = kind.get("kind")
    if what == "rowid":
        return row
    if what == "cycle":
        return (row % int(kind["modulo"])) + int(kind.get("offset", 0))
    if what == "choice":
        values = kind["values"]
        return values[row % len(values)] if kind.get("cycle") else rng.choice(values)
    if what == "text":
        return "".join(rng.choice(alphabet) for _ in range(int(kind["length"])))
    if what == "const":
        return kind["value"]
    if what == "int_range":
        return rng.randint(int(kind["low"]), int(kind["high"]))
    _reject("unsupported seed column kind %r" % what)


def check_seed(seed: Any) -> Sequence[Mapping[str, Any]]:
    """Validate a seed specification without executing any of it."""
    if not isinstance(seed, (list, tuple)) or not seed:
        _reject("database.seed must list at least one table to populate")
    for table in seed:
        if not isinstance(table, dict) or not isinstance(table.get("table"), str):
            _reject("each seed entry must be an object naming a table")
        if "values" in table:
            columns, values = table.get("columns"), table["values"]
            if (not isinstance(columns, list) or not columns
                    or not all(isinstance(c, str) for c in columns)):
                _reject("a literal seed must list its column names")
            if not isinstance(values, list) or not values:
                _reject("a literal seed must list at least one row")
            for row in values:
                if not isinstance(row, list) or len(row) != len(columns):
                    _reject("every literal seed row must match its column count")
            continue
        rows = table.get("rows")
        if not isinstance(rows, int) or isinstance(rows, bool) or not 0 < rows <= MAX_ROWS:
            _reject("%s must declare rows between 1 and %d" % (table["table"], MAX_ROWS))
        columns = table.get("columns")
        if not isinstance(columns, dict) or not columns:
            _reject("%s must declare its seed columns" % table["table"])
        for name, kind in columns.items():
            if not isinstance(kind, dict) or kind.get("kind") not in SEED_KINDS:
                _reject("%s.%s must use one of these seed kinds: %s"
                        % (table["table"], name, ", ".join(SEED_KINDS)))
    return seed


def build(schema_sql: str, seed: Sequence[Mapping[str, Any]], dest: str) -> dict:
    """Create the repository's database at `dest`. Deterministic."""
    statements = check_schema(schema_sql)
    check_seed(seed)
    if os.path.exists(dest):
        os.remove(dest)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)

    rng = random.Random(_RNG_SEED)
    alphabet = string.ascii_lowercase + string.digits
    written = {}

    conn = sqlite3.connect(dest)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        for statement in statements:
            conn.execute(statement)

        for table in seed:
            name = table["table"]
            if "values" in table:
                columns = table["columns"]
                rows = [tuple(row) for row in table["values"]]
            else:
                columns = list(table["columns"])
                rows = [
                    tuple(_column_value(table["columns"][c], index, rng, alphabet)
                          for c in columns)
                    for index in range(1, int(table["rows"]) + 1)
                ]
            placeholders = ",".join("?" for _ in columns)
            conn.executemany(
                'INSERT INTO %s (%s) VALUES (%s)'
                % (name, ",".join(columns), placeholders), rows)
            written[name] = len(rows)

        conn.execute("ANALYZE")
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    except sqlite3.Error as exc:
        conn.close()
        _reject("the repository's schema or seed could not be applied: %s" % exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {"path": dest, "tables": written, "bytes": os.path.getsize(dest)}
