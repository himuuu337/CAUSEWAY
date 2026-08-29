"""Data access for the order service.

Every query here runs on the audit endpoint's hot path.
"""
from __future__ import annotations

import sqlite3


def connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect(path)


def lookup_order_audit(conn: sqlite3.Connection, order_id: int) -> dict:
    """Summarise the audit trail for one order."""
    events, payload_bytes = conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0)
        FROM order_audit
        WHERE order_id + 0 = ?
    """, (order_id,)).fetchone()
    return {"order_id": order_id, "events": events, "payload_bytes": payload_bytes}


def lookup_status_label(conn: sqlite3.Connection, code: str) -> str:
    """Resolve a status code to its display label."""
    row = conn.execute("""
        SELECT label
        FROM status_label
        WHERE UPPER(code) = ?
    """, (code.upper(),)).fetchone()
    return row[0] if row else code


def audit_page(conn: sqlite3.Connection, order_ids) -> list:
    """One page of the order audit view: a summary and a status label per order."""
    page = []
    for order_id in order_ids:
        summary = lookup_order_audit(conn, order_id)
        summary["status"] = lookup_status_label(conn, _status_code_for(order_id))
        page.append(summary)
    return page


def _status_code_for(order_id: int) -> str:
    codes = ("CREATED", "PRICED", "RESERVED", "PACKED", "SHIPPED", "SETTLED")
    return codes[order_id % len(codes)]


def insert_order(conn: sqlite3.Connection, order_id: int, quantity: int) -> int:
    """Record a new order. The caller decides what quantity is acceptable -
    this just writes what it is given."""
    cursor = conn.execute(
        "INSERT INTO orders (order_id, quantity, created_at) VALUES (?, ?, ?)",
        (order_id, quantity, "2026-08-29T00:00:00Z"))
    conn.commit()
    return cursor.lastrowid
