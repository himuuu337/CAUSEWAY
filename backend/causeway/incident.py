"""The incident under investigation, and the deploy record around it.

This is data, not logic. It stands in for what a real Causeway would pull from
an incident tracker and a deployment log.

The demo incident: two changes shipped to order-service inside the same
fifteen-minute window, both perfectly correlated with a latency regression.

    A  refactor/order-query-batching     9 files, 412 lines   innocent
    B  perf/normalise-audit-predicate    1 file,    3 lines   the cause

B wraps order_id in an expression inside the audit predicate, which makes the
index on order_audit(order_id) unusable and turns every lookup into a full
table scan. A is a large, alarming-looking refactor that issues exactly the
same queries as the code it replaced.

Every observational signal - diff size, blast radius, files touched on the hot
path - points at A. Only an experiment separates them.
"""
from __future__ import annotations

INCIDENT = {
    "id": "INCIDENT-001",
    "title": "Order Service Latency Incident",
    "service": "order-service",
    "symptom": "p95 latency on the order audit endpoint",
    "detected_at": "2026-08-28T14:05:00Z",
    # The window a deploy must fall inside to be considered a candidate.
    "window_seconds": 900,
    # The code path that got slow. Used by the observational baseline to score
    # how much of the hot path each change touched.
    "hot_path_files": ["app/queries.py", "app/service.py"],
}

DEPLOYS = [
    {
        "change_id": "A",
        "sha": "4f1c9ab",
        "branch": "refactor/order-query-batching",
        "service": "order-service",
        "deployed_at": "2026-08-28T14:02:54Z",
        "summary": "Route audit lookups through a batching helper",
        "author": "platform-team",
        "files_changed": 9,
        "lines_changed": 412,
        "changed_files": [
            "app/queries.py", "app/service.py", "app/batching/__init__.py",
            "app/batching/collector.py", "app/batching/flatten.py",
            "app/batching/dedupe.py", "app/models/summary.py",
            "tests/test_batching.py", "docs/batching.md",
        ],
    },
    {
        "change_id": "B",
        "sha": "9de20f4",
        "branch": "perf/normalise-audit-predicate",
        "service": "order-service",
        "deployed_at": "2026-08-28T14:02:18Z",
        "summary": "Normalise order_id inside the audit predicate",
        "author": "platform-team",
        "files_changed": 1,
        "lines_changed": 3,
        "changed_files": ["app/queries.py"],
    },
    {
        "change_id": "C",
        "sha": "77b1e05",
        "branch": "chore/bump-billing-sdk",
        "service": "billing-service",
        "deployed_at": "2026-08-28T14:01:10Z",
        "summary": "Bump the billing SDK to 4.2.1",
        "author": "payments-team",
        "files_changed": 2,
        "lines_changed": 18,
        "changed_files": ["billing/client.py", "requirements.txt"],
    },
    {
        "change_id": "D",
        "sha": "1a55cc8",
        "branch": "feat/order-export-csv",
        "service": "order-service",
        "deployed_at": "2026-08-28T11:40:00Z",
        "summary": "Add CSV export to the order report page",
        "author": "reporting-team",
        "files_changed": 4,
        "lines_changed": 96,
        "changed_files": ["app/export.py", "app/routes.py",
                          "templates/report.html", "tests/test_export.py"],
    },
]


def deploy_record() -> dict:
    return {"incident": dict(INCIDENT), "deploys": [dict(d) for d in DEPLOYS]}
