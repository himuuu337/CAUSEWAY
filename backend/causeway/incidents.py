"""Live incidents: a confirmed HIGH risk assessment, captured once, and
(if a repository is registered for the service) handed to the existing
investigation machinery - never a new one built for this purpose.

Not causeway.incident (singular) - that module is the bundled A/B
demonstration's fabricated deploy record, a different thing entirely, kept
deliberately unreachable from the repository investigation path. This
module is real: an Incident here is created only from a real, hysteresis-
confirmed RiskAssessment, and the investigation it triggers is the same
causeway.runs.manager every other investigation in this project already
goes through.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Optional, Tuple

from causeway import services as services_module
from causeway.prediction.schema import RiskAssessment
from causeway.repository.errors import RepositoryRejected

AWAITING_REPOSITORY_CONTEXT = "AWAITING_REPOSITORY_CONTEXT"
INVESTIGATION_STARTED = "INVESTIGATION_STARTED"
INVESTIGATION_ALREADY_RUNNING = "INVESTIGATION_ALREADY_RUNNING"
REGISTRATION_REJECTED = "REGISTRATION_REJECTED"


@dataclass(frozen=True)
class Incident:
    incident_id: str
    service: str
    detector: str
    predicted_failure: str
    risk_score: float
    evidence: Tuple[str, ...]
    current_values: Mapping[str, float]
    trends: Mapping[str, float]
    eta_seconds: Optional[float]
    sample_count: int
    created_at: float
    kind: str = "predicted_failure"
    status: str = AWAITING_REPOSITORY_CONTEXT
    run_id: Optional[str] = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "incident_id": self.incident_id, "service": self.service,
            "kind": self.kind, "detector": self.detector,
            "predicted_failure": self.predicted_failure, "risk_score": self.risk_score,
            "evidence": list(self.evidence), "telemetry_window": {
                "current_values": dict(self.current_values), "trends": dict(self.trends),
                "eta_seconds": self.eta_seconds, "sample_count": self.sample_count,
            },
            "created_at": self.created_at, "status": self.status, "run_id": self.run_id,
            "detail": self.detail,
        }


class IncidentManager:
    """Edge-triggered: an incident is created only the moment a detector's
    `confirmed` flag turns true, and cleared from the "currently open" set
    only when it turns false again - never once per HIGH evaluation, which
    would mean a duplicate incident every few seconds an outage lasts."""

    def __init__(self, run_starter=None, service_registry=None):
        self._lock = threading.Lock()
        self._incidents: List[Incident] = []
        self._open: Dict[Tuple[str, str], str] = {}     # (service, detector) -> incident_id
        self._last_confirmed: Dict[Tuple[str, str], bool] = {}
        self._run_starter = run_starter
        self._services = service_registry if service_registry is not None \
            else services_module.registry

    def observe(self, assessments: List[RiskAssessment]) -> List[Incident]:
        created: List[Incident] = []
        with self._lock:
            for assessment in assessments:
                key = (assessment.service, assessment.detector)
                previous = self._last_confirmed.get(key, False)
                self._last_confirmed[key] = assessment.confirmed
                if assessment.confirmed and not previous:
                    incident = self._create(assessment)
                    created.append(incident)
                elif not assessment.confirmed and previous:
                    self._open.pop(key, None)
        return [self._handoff(incident) for incident in created]

    def _create(self, assessment: RiskAssessment) -> Incident:
        incident = Incident(
            incident_id=uuid.uuid4().hex[:12], service=assessment.service,
            detector=assessment.detector, predicted_failure=assessment.predicted_failure,
            risk_score=assessment.score, evidence=assessment.evidence,
            current_values=assessment.current_values, trends=assessment.trends,
            eta_seconds=assessment.eta_seconds, sample_count=assessment.sample_count,
            created_at=time.time(),
        )
        self._incidents.append(incident)
        self._open[(assessment.service, assessment.detector)] = incident.incident_id
        return incident

    def _replace(self, incident: Incident, **changes) -> Incident:
        updated = replace(incident, **changes)
        with self._lock:
            for index, existing in enumerate(self._incidents):
                if existing.incident_id == incident.incident_id:
                    self._incidents[index] = updated
                    break
        return updated

    def _handoff(self, incident: Incident) -> Incident:
        """If a repository is registered for this service, start the SAME
        investigation any other repository run in this project starts -
        never a bespoke one. Never clones anything for a service nobody
        registered a target for."""
        target = self._services.get(incident.service)
        if target is None:
            return self._replace(incident, status=AWAITING_REPOSITORY_CONTEXT)

        starter = self._run_starter or _default_run_starter
        try:
            run = starter(repository_url=target.repository_url,
                          instruction=_incident_instruction(incident),
                          mode=target.investigation_mode)
        except RepositoryRejected as exc:
            return self._replace(incident, status=REGISTRATION_REJECTED, detail=str(exc))
        except Exception as exc:                       # noqa: BLE001
            # AlreadyRunning from causeway.runs, imported lazily to avoid a
            # hard dependency cycle at module load time - one investigation
            # at a time is the existing rule, and an incident that arrives
            # while one is running does not queue a second.
            from causeway.runs import AlreadyRunning
            if isinstance(exc, AlreadyRunning):
                return self._replace(incident, status=INVESTIGATION_ALREADY_RUNNING,
                                    detail=str(exc))
            raise
        return self._replace(incident, status=INVESTIGATION_STARTED, run_id=run.id)

    def all(self) -> List[Incident]:
        with self._lock:
            return list(self._incidents)

    def open_for(self, service: str) -> List[Incident]:
        with self._lock:
            ids = {v for (s, _d), v in self._open.items() if s == service}
            return [i for i in self._incidents if i.incident_id in ids]

    def reset(self) -> None:
        with self._lock:
            self._incidents.clear()
            self._open.clear()
            self._last_confirmed.clear()


def _incident_instruction(incident: Incident) -> str:
    lines = ["Investigate and, if a real cause can be established, fix a predicted "
            "%s." % incident.predicted_failure,
            "Runtime evidence: " + "; ".join(incident.evidence) + "."]
    if incident.eta_seconds is not None:
        lines.append("Estimated time to threshold at the current trend: %.0fs."
                     % incident.eta_seconds)
    return " ".join(lines)


def _default_run_starter(**kwargs):
    from causeway.runs import manager
    return manager.start(**kwargs)


manager = IncidentManager()

__all__ = ["Incident", "IncidentManager", "manager", "AWAITING_REPOSITORY_CONTEXT",
          "INVESTIGATION_STARTED", "INVESTIGATION_ALREADY_RUNNING",
          "REGISTRATION_REJECTED"]
