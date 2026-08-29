-- order-service schema.
--
-- Both order_audit.order_id and status_label.code are indexed. That matters:
-- a predicate that wraps an indexed column in an expression cannot use its
-- index. Two places in db.py do exactly that, and from here they look the same.

DROP TABLE IF EXISTS order_audit;
CREATE TABLE order_audit (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    action     TEXT    NOT NULL,
    payload    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX idx_audit_order ON order_audit(order_id);

DROP TABLE IF EXISTS status_label;
CREATE TABLE status_label (
    code  TEXT PRIMARY KEY,
    label TEXT NOT NULL
);
CREATE INDEX idx_status_code ON status_label(code);

-- Orders placed through POST /orders. Empty until the service writes to it -
-- there is no seed for this table.
DROP TABLE IF EXISTS orders;
CREATE TABLE orders (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL,
    quantity   INTEGER NOT NULL,
    created_at TEXT    NOT NULL
)
