-- This demo's incident lives in an in-memory connection pool, not in a
-- query - this table exists only to satisfy the causeway.json database
-- contract, and app.py touches it only to prove the seeded file is real.

DROP TABLE IF EXISTS status_label;
CREATE TABLE status_label (
    code  TEXT PRIMARY KEY,
    label TEXT NOT NULL
)
