"""Where a telemetry sample turns into an investigation, one visible step
at a time.

    sample arrives
      -> stored (causeway.telemetry)
      -> every detector evaluated, hysteresis applied (causeway.prediction)
      -> a detector that just turned confirmed opens an Incident, and - if
         a repository is registered for the service - hands off to the
         SAME investigation every other run in this project goes through
         (causeway.incidents, causeway.runs)
      -> every step published to the live monitor feed (causeway.monitor)

This module owns none of that state itself; it only sequences the existing
owners of each piece, which is what keeps a telemetry POST cheap - the
investigation it may trigger runs on its own thread (causeway.runs already
does that), so ingest() returns as soon as the plan is set in motion, not
when a Gemini call anywhere in it finishes.
"""
from __future__ import annotations

import time
from typing import List, Optional

from causeway import monitor as monitor_module
from causeway.incidents import Incident, manager as default_incident_manager
from causeway.prediction.engine import PredictionEngine, engine as default_prediction_engine
from causeway.prediction.schema import HIGH, RiskAssessment
from causeway.telemetry.schema import TelemetrySample
from causeway.telemetry.store import TelemetryStore, store as default_telemetry_store


def ingest(sample: TelemetrySample, *,
          telemetry: TelemetryStore = None,
          predictor: PredictionEngine = None,
          incidents=None,
          monitor: monitor_module.MonitorManager = None) -> dict:
    """Store one sample and run the whole pipeline synchronously - it is
    cheap by construction (no model call sits on this path; see the module
    docstring) - publishing every step to the monitor feed. Returns a small
    summary, mainly useful for tests and for a caller that wants the risk
    picture without also opening an SSE connection."""
    telemetry = telemetry if telemetry is not None else default_telemetry_store
    predictor = predictor if predictor is not None else default_prediction_engine
    incidents = incidents if incidents is not None else default_incident_manager
    monitor = monitor if monitor is not None else monitor_module.manager

    telemetry.append(sample)
    monitor.publish({"type": monitor_module.TELEMETRY_RECEIVED, "service": sample.service,
                     "timestamp": sample.timestamp, "values": dict(sample.values),
                     "t": round(time.time(), 3)})

    assessments: List[RiskAssessment] = predictor.evaluate(sample.service)
    for assessment in assessments:
        monitor.publish(dict({"type": monitor_module.RISK_UPDATED, "t": round(time.time(), 3)},
                            **assessment.as_dict()))
        if assessment.level == HIGH:
            monitor.publish({"type": monitor_module.FAILURE_PREDICTED,
                             "service": assessment.service, "detector": assessment.detector,
                             "predicted_failure": assessment.predicted_failure,
                             "score": assessment.score, "confirmed": assessment.confirmed,
                             "t": round(time.time(), 3)})

    created: List[Incident] = incidents.observe(assessments)
    for incident in created:
        monitor.publish(dict({"type": monitor_module.INCIDENT_CREATED,
                             "t": round(time.time(), 3)}, **incident.as_dict()))
        monitor.publish({"type": monitor_module.INVESTIGATION_HANDOFF,
                         "incident_id": incident.incident_id, "service": incident.service,
                         "status": incident.status, "run_id": incident.run_id,
                         "t": round(time.time(), 3)})

    return {"service": sample.service,
           "assessments": [a.as_dict() for a in assessments],
           "incidents_created": [i.incident_id for i in created]}
