# causeway-order-demo

A small order service that follows the [Causeway](https://github.com/) repository
contract (`causeway.json`), so Causeway has a real repository to clone, run,
investigate and patch.

The service is genuinely here: `app.py` serves the audit endpoint, `db.py`
queries the database, `schema.sql` defines it, and `workload.json` is the
traffic Causeway replays. Causeway builds the database from this repository's
own schema and seed declaration - it does not bring its own data.

## The incident

The order audit endpoint got slow. Both queries on its hot path wrap an
indexed column in an expression:

    db.py  lookup_order_audit()    WHERE order_id + 0 = ?
    db.py  lookup_status_label()   WHERE UPPER(code) = ?

`order_audit.order_id` and `status_label.code` are both indexed, so static
analysis flags both, and it is right to: a wrapped column cannot be matched
against an index, and either one *could* be the problem.

They are not the same, and nothing in this repository says which is which.
`order_audit` holds 40,000 rows, so scanning it costs real time.
`status_label` holds six, so scanning it costs nothing measurable. Only
running the experiment tells them apart - which is the point.

## What this manifest does and does not say

`causeway.json` declares capabilities and safe inputs: the runtime, the
entrypoint, which files may be analysed, which may be patched, the schema and
seed for the database, and the workload to replay.

It does **not** name a root cause, a correct hypothesis, or a repair. There is
no deploy history and no answer key. Causeway has to find the suspects by
reading the source, and has to settle between them by measuring.

## Safety

No command strings anywhere in the manifest. The schema may only create and
drop tables, indexes and views; seed columns come from a closed set of kinds.
Causeway runs `python app.py` and nothing else.
