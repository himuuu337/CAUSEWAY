"""The observational baseline: ranking candidates without an experiment.

WHAT THIS IS. A correlation-only scorer built for this controlled demo. Given
the deploy record and the incident, it ranks changes the way an observational
approach must: by how well each one correlates with the failure. Same service,
how recently it shipped, how large the diff is, how much of the slow code path
it touched.

WHAT THIS IS NOT. It is not a model of any commercial RCA product, and no
claim is made that real tools use this arithmetic. It is a deliberately
simple, deliberately honest stand-in for the class of reasoning that has only
observational evidence to work with - included so the contrast with an
experiment is visible rather than asserted.

It gets this incident wrong, and it gets it wrong for the right reason: a
three-line change caused the outage and a 412-line change did not, and no
amount of correlational evidence can tell those two apart.

STRUCTURALLY BLIND. This module cannot reach the sandbox, the replay, the
measurements or the verdict, and a test walks its import graph to keep it that
way. Its blindness is a property of the code, not a promise in a comment.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence

# What the baseline believes matters, and how much. Diff magnitude carries the
# most weight - which is exactly the assumption this demo exists to falsify.
WEIGHTS = {
    "same_service": 0.20,
    "recency": 0.20,
    "magnitude": 0.35,
    "hot_path_overlap": 0.25,
}

# A 500-line change is treated as "as large as it gets" for scoring purposes.
MAGNITUDE_CEILING = 500


@dataclass(frozen=True)
class Assessment:
    change_id: str
    branch: str
    score: float
    components: Dict[str, float]
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _recency(candidate, window_seconds: int) -> float:
    """1.0 for a deploy at the moment of detection, 0.0 at the window edge."""
    gap = max(0, candidate.seconds_before_detection)
    return max(0.0, 1.0 - gap / float(window_seconds))


def _magnitude(candidate) -> float:
    """Diff size on a log scale, so 412 lines is 'big' without 4000 being 10x
    bigger. This is the term that decides the ranking, and it is wrong."""
    return min(1.0, math.log1p(candidate.lines_changed)
               / math.log1p(MAGNITUDE_CEILING))


def _hot_path_overlap(candidate, hot_path_files: Sequence[str]) -> float:
    """How much of the code path that got slow this change touched."""
    if not hot_path_files:
        return 0.0
    touched = set(candidate.changed_files) & set(hot_path_files)
    return len(touched) / float(len(hot_path_files))


def assess(candidate, incident: dict) -> Assessment:
    components = {
        "same_service": 1.0 if candidate.service == incident["service"] else 0.0,
        "recency": _recency(candidate, int(incident["window_seconds"])),
        "magnitude": _magnitude(candidate),
        "hot_path_overlap": _hot_path_overlap(candidate,
                                              incident.get("hot_path_files", ())),
    }
    score = sum(WEIGHTS[name] * value for name, value in components.items())
    reason = (
        "shipped to %s %d min %02d s before detection, %d lines across %d "
        "file%s, touching %d of %d files on the slow path"
        % (candidate.service,
           candidate.seconds_before_detection // 60,
           candidate.seconds_before_detection % 60,
           candidate.lines_changed, candidate.files_changed,
           "" if candidate.files_changed == 1 else "s",
           len(set(candidate.changed_files) & set(incident.get("hot_path_files", ()))),
           len(incident.get("hot_path_files", ())))
    )
    return Assessment(candidate.change_id, candidate.branch,
                      round(score, 3),
                      {k: round(v, 3) for k, v in components.items()},
                      reason)


def rank(candidates, incident: dict) -> List[Assessment]:
    """Highest correlation first. Takes no measurement, and could not accept
    one: nothing in this module's signature or imports can carry a result."""
    return sorted((assess(c, incident) for c in candidates),
                  key=lambda a: (-a.score, a.change_id))


def top_suspect(assessments: Sequence[Assessment]) -> str:
    return assessments[0].change_id if assessments else ""
