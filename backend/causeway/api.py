"""The HTTP surface.

One investigation runs at a time and its events are streamed to the browser
over Server-Sent Events, in the order the engine emitted them and with their
content unchanged. The interface renders what it is sent; it does not compute
verdicts, and it is not given the means to.

    GET  /api/health                     is the backend up, and is it seeded
    GET  /api/status                     what the current investigation is doing
    POST /api/investigation              start one (409 if one is running)
    GET  /api/investigation/stream       SSE, resumable
    GET  /api/investigation/{id}/events  the whole buffer as JSON

This module is deliberately thin. The awkward behaviour - resuming, closing,
not hanging on a finished run - lives in causeway/stream.py, where it can be
executed on its own; everything here is wiring.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from causeway import config, intent, monitor, production, stream, verdict
from causeway.graph import build_graph
from causeway.incident import INCIDENT
from causeway.incidents import manager as incident_manager
from causeway.prediction.engine import engine as prediction_engine
from causeway.runs import AlreadyRunning, manager
from causeway.services import registry as service_registry
from causeway.telemetry import TelemetryRejected, validate_sample
from causeway.telemetry.store import store as telemetry_store

FRONTEND_DIST = os.path.join(os.path.dirname(config.BACKEND_ROOT),
                             "frontend", "dist")

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx and friends buffer text/event-stream by default, which turns a
    # live investigation into one lump at the end
    "X-Accel-Buffering": "no",
}

app = FastAPI(title="Causeway", version="0.2.0",
              description="Experimental root-cause verification")

# The built frontend is served from this same process, so a demo is one URL.
# These origins are for `npm run dev`, where Vite serves the page on 5173.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# -------------------------------------------------------------------- health

@app.get("/api/health")
def health() -> dict:
    seeded = config.is_ready()
    return {
        "status": "ok" if seeded else "not-seeded",
        "seeded": seeded,
        "hint": None if seeded else "run: python -m causeway.cli seed",
        "incident": {"id": INCIDENT["id"], "service": INCIDENT["service"],
                     "title": INCIDENT["title"]},
        "engine": {
            "phases": list(verdict.PHASES),
            "verdicts": [verdict.PROVEN, verdict.REFUTED,
                         verdict.SUPPORTED, verdict.UNRESOLVED],
            "failure_factor": verdict.FAILURE_FACTOR,
            "recovery_factor": verdict.RECOVERY_FACTOR,
        },
        "frontend_built": os.path.isdir(FRONTEND_DIST),
    }


@app.get("/api/status")
def status() -> dict:
    return manager.status()


# ------------------------------------------------------------------ telemetry
#
#     POST /api/telemetry           ingest one real sample, cheap, synchronous
#     GET  /api/prediction/status   the current risk picture for one service
#     POST /api/services/register   link a service name to its GitHub repository
#     GET  /api/services            every registered link
#     GET  /api/monitor/stream      SSE: telemetry_received, risk_updated,
#                                    failure_predicted, incident_created,
#                                    investigation_handoff
#
# causeway.production.ingest does the real work (store -> evaluate -> maybe
# open an incident -> maybe hand off) - this is wiring, exactly like
# /api/investigation below is wiring around causeway.runs.

@app.post("/api/telemetry")
def post_telemetry(payload: dict = Body(...)):
    try:
        sample = validate_sample(payload, now=time.time())
    except TelemetryRejected as exc:
        raise HTTPException(status_code=400, detail={"reason": "rejected", "message": str(exc)})
    summary = production.ingest(sample, telemetry=telemetry_store, predictor=prediction_engine,
                                incidents=incident_manager, monitor=monitor.manager)
    return JSONResponse(status_code=200, content=summary)


@app.get("/api/prediction/status")
def prediction_status(service: Optional[str] = Query(None)):
    if service:
        return prediction_engine.status(service)
    return {"services": [prediction_engine.status(name) for name in telemetry_store.services()]}


@app.post("/api/services/register")
def register_service(payload: dict = Body(...)):
    service = payload.get("service")
    repository_url = payload.get("repository_url")
    if not isinstance(service, str) or not service.strip():
        raise HTTPException(status_code=400,
                            detail={"reason": "bad-request", "message": "service is required"})
    if not isinstance(repository_url, str) or not repository_url.strip():
        raise HTTPException(
            status_code=400,
            detail={"reason": "bad-request", "message": "repository_url is required"})
    try:
        target = service_registry.register(
            service, repository_url, branch=payload.get("branch") or "",
            investigation_mode=payload.get("investigation_mode") or intent.DIAGNOSE_AND_FIX)
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail={"reason": "bad-request", "message": str(exc)})
    except Exception as exc:                       # RepositoryRejected
        raise HTTPException(status_code=400,
                            detail={"reason": "rejected", "message": str(exc)})
    return JSONResponse(status_code=200, content=target.as_dict())


@app.get("/api/services")
def list_services():
    return {"services": [t.as_dict() for t in service_registry.all().values()]}


@app.get("/api/monitor/stream")
async def monitor_stream(
    request: Request,
    from_index: int = Query(0, alias="from"),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    return StreamingResponse(
        monitor.monitor_stream(
            monitor.manager,
            start_index=stream.resume_index(from_index, last_event_id),
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# ------------------------------------------------------------- investigation

@app.post("/api/investigation")
def start_investigation(payload: Optional[dict] = Body(default=None)):
    # An optional {"repository_url": "..."} body. Absent, empty, or no body
    # at all - exactly what today's frontend already sends, and exactly what
    # a direct call (as the test suite makes) passes too, since `payload`
    # is then the FastAPI Body() placeholder rather than a dict - means the
    # bundled demo, same as before this field existed. A malformed JSON body
    # never reaches here when served over HTTP: FastAPI rejects it with a
    # 422 before this function runs.
    body = payload if isinstance(payload, dict) else {}
    repository_url = body.get("repository_url") or None
    if repository_url is not None and not isinstance(repository_url, str):
        raise HTTPException(
            status_code=400,
            detail={"reason": "bad-request", "message": "repository_url must be a string"})

    # The user's own instruction, and the mode they picked in the interface if
    # they picked one. Both are carried verbatim to causeway.intent, which is
    # the only thing that reads them; an unknown mode is rejected here rather
    # than quietly reinterpreted, because guessing at a mode is how a run that
    # was told to change nothing ends up changing something.
    instruction = body.get("instruction") or None
    if instruction is not None and not isinstance(instruction, str):
        raise HTTPException(
            status_code=400,
            detail={"reason": "bad-request", "message": "instruction must be a string"})
    mode = body.get("mode") or None
    if mode is not None and (not isinstance(mode, str) or mode not in intent.MODES):
        raise HTTPException(
            status_code=400,
            detail={"reason": "bad-request",
                    "message": "mode must be one of %s" % ", ".join(intent.MODES)})

    # Seeding is the BUNDLED demo's precondition, not the product's: it
    # builds Causeway's own template database. A repository brings its own
    # schema and seed and builds its own database inside its own disposable
    # workspace, so an unseeded machine can still investigate a repository.
    if not repository_url and not config.is_ready():
        raise HTTPException(
            status_code=503,
            detail={"reason": "not-seeded",
                    "message": "this machine is not seeded yet",
                    "hint": "run: python -m causeway.cli seed"})

    try:
        run = manager.start(repository_url=repository_url, instruction=instruction,
                            mode=mode)
    except AlreadyRunning as exc:
        # Not an error anyone needs to act on: the client attaches to the run
        # that is already in progress rather than being told no.
        #
        # The run summary is spread FIRST and the explicit keys last. The other
        # way round, status()'s own `error` field - empty while a run is
        # healthy - silently overwrote the reason for the 409, and the client
        # got a conflict with no explanation in it.
        return JSONResponse(
            status_code=409,
            content={**manager.status(),
                     "reason": "already-running",
                     "run_id": exc.run_id,
                     "message": str(exc)})
    return JSONResponse(status_code=202, content=run.summary())


@app.get("/api/investigation/{run_id}/events")
def all_events(run_id: str) -> dict:
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such investigation")
    return {**run.summary(), "events": manager.events_from(run_id, 0)}


@app.get("/api/investigation/{run_id}/graph")
def investigation_graph(run_id: str) -> dict:
    """The causal graph, built deterministically from this run's own event
    buffer - see causeway/graph.py. Nothing is computed here beyond finding
    the run and its confirmed-incident linkage; build_graph decides nothing
    causeway.verdict did not already decide."""
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such investigation")
    events = manager.events_from(run_id, 0)
    return build_graph(events, incidents=incident_manager.all(), run_id=run_id)


@app.get("/api/investigation/stream")
async def investigation_stream(
    request: Request,
    run_id: Optional[str] = Query(None),
    from_index: int = Query(0, alias="from"),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
):
    """Follow an investigation, resumably."""
    run = manager.get(run_id) if run_id else manager.run
    if run is None:
        raise HTTPException(status_code=404,
                            detail="no investigation to stream - start one first")

    return StreamingResponse(
        stream.event_stream(
            manager, run.id,
            start_index=stream.resume_index(from_index, last_event_id),
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# ------------------------------------------------------------------ frontend

if os.path.isdir(FRONTEND_DIST):
    assets = os.path.join(FRONTEND_DIST, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))


def main():
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="causeway-api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("causeway.api:app" if args.reload else app,
                host=args.host, port=args.port, reload=args.reload,
                log_level="info")


if __name__ == "__main__":
    main()
