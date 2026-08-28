"""Deterministic candidate localisation.

Given the deploy record and the incident, decide which changes are even worth
considering. This is a filter, not a diagnosis: it narrows the field on facts
- which service, which window - and records why everything else was dropped,
so exclusions can be shown rather than assumed.

No model, no scoring, no experiment.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Tuple

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Candidate:
    change_id: str
    sha: str
    branch: str
    service: str
    summary: str
    deployed_at: str
    seconds_before_detection: int
    files_changed: int
    lines_changed: int
    changed_files: Tuple[str, ...]

    def as_dict(self) -> dict:
        data = asdict(self)
        data["changed_files"] = list(self.changed_files)
        return data


@dataclass(frozen=True)
class Exclusion:
    change_id: str
    branch: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def localize(record: dict) -> Tuple[List[Candidate], List[Exclusion]]:
    incident = record["incident"]
    detected = parse_time(incident["detected_at"])
    window = int(incident["window_seconds"])
    service = incident["service"]

    candidates, excluded = [], []
    for deploy in record["deploys"]:
        deployed = parse_time(deploy["deployed_at"])
        gap = (detected - deployed).total_seconds()

        if deploy["service"] != service:
            excluded.append(Exclusion(
                deploy["change_id"], deploy["branch"],
                "deployed to %s, not the affected service %s"
                % (deploy["service"], service)))
            continue
        if gap < 0:
            excluded.append(Exclusion(
                deploy["change_id"], deploy["branch"],
                "deployed after the incident was detected"))
            continue
        if gap > window:
            excluded.append(Exclusion(
                deploy["change_id"], deploy["branch"],
                "deployed %d min before detection, outside the %d min window"
                % (gap // 60, window // 60)))
            continue

        candidates.append(Candidate(
            change_id=deploy["change_id"], sha=deploy["sha"],
            branch=deploy["branch"], service=deploy["service"],
            summary=deploy["summary"], deployed_at=deploy["deployed_at"],
            seconds_before_detection=int(gap),
            files_changed=deploy["files_changed"],
            lines_changed=deploy["lines_changed"],
            changed_files=tuple(deploy["changed_files"]),
        ))

    candidates.sort(key=lambda c: c.change_id)
    excluded.sort(key=lambda e: e.change_id)
    return candidates, excluded
