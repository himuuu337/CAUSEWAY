# Causeway

**Experimental root-cause verification.**

Most root-cause tools analyse telemetry and rank likely causes. Causeway takes
the next step: it turns a suspected cause into a testable hypothesis, designs a
controlled experiment, runs it in an isolated sandbox against the same replayed
workload, and lets the measurement decide.

    AI PROPOSES.  CODE VALIDATES.  SYSTEM EXPERIMENTS.  MEASUREMENTS DECIDE.

A language model may propose a hypothesis and design an experiment. It may
never decide whether a candidate is the cause. That is not a promise in a
comment - `backend/tests/test_no_model_in_verdict.py` walks the import graph of
the module that produces the verdict and fails the build if anything
model-shaped, networked or planner-shaped becomes reachable from it.

## Status: Milestone 4

The causal core, a live investigation streamed to a browser, and the dashboard
that explains it.

| | |
|---|---|
| Milestone 1 | causal core, CLI-verified · **done** |
| Milestone 2 | API + SSE + frontend shell · **done** |
| Milestone 3 | the investigation dashboard · **done** |
| Milestone 4 | Gemini plans the experiments · **done** |
| Milestone 5 | fix generation and fix verification (stretch) |

## What the dashboard shows, and what it is not allowed to do

The page builds itself from Server-Sent Events while a real investigation runs:
the incident's measured latency, the localised candidates, the observational
ranking, the experiment plan and its provenance, the validator's checks, then
seven measured phases per candidate, then the verdicts, then the contrast.

The rule the interface is built around is that it renders and never reasons:

- **No verdict is computed in the browser.** `PROVEN` appears because the
  backend emitted `{"type": "verdict", "verdict": "PROVEN"}`. The frontend has
  no thresholds, no ratios and no decision table.
- **No phase state is inferred.** `BROKEN` and `HEALTHY` arrive on
  `phase_judged` events, already decided against the control the engine
  measured beside that phase.
- **Nothing appears before the event that carries it.** Bars are empty until a
  measurement lands; a verdict pill reads `MEASURING…` until the verdict
  arrives. A backend test asserts no verdict word appears anywhere in the
  stream before the first measurement.
- **The planner is labelled by provenance, never by assumption.** A
  deterministic run is called a *Deterministic Planner*; only a run where
  `used_fallback` is true is called a *Deterministic Fallback*; and nothing is
  ever called Gemini unless the backend reported `kind: "gemini"`.

Bar heights are the one thing the page derives, and only as a proportion of
the largest measurement in the same experiment. Every number printed beside
them is the backend's.

## Running it

Python 3.10+ and Node 18+.

    cd backend
    pip install -r requirements.txt
    python -m causeway.cli seed          # size the sandbox to this machine
    python -m unittest discover -s tests -t . -v

    cd ..\frontend
    npm install
    npm run build                        # tsc --noEmit && vite build

Then one process, one URL - this is the demo-day path:

    cd ..\backend
    python -m causeway.api               # http://127.0.0.1:8000

While iterating, two processes instead (`run-dev.ps1` starts both):

    cd backend  && python -m causeway.api
    cd frontend && npm run dev           # http://127.0.0.1:5173, proxies /api

The command line still works and needs no dependencies at all:

    python -m causeway.cli investigate   # the full investigation, in the terminal
    python -m causeway.cli events        # the same run as raw NDJSON

## The API

| | |
|---|---|
| `GET /api/health` | is the backend up, is this machine seeded, what thresholds is the engine using |
| `GET /api/status` | what the current investigation is doing |
| `POST /api/investigation` | start one. `202` with a run id, or `409` naming the run already in progress |
| `GET /api/investigation/stream` | Server-Sent Events, resumable |
| `GET /api/investigation/{id}/events` | the whole buffer as JSON |

One investigation runs at a time, on purpose: the sandbox is a real process
measuring real latency, and two investigations sharing a machine would measure
each other.

Every SSE frame carries its buffer index as `id:`, so a browser that
reconnects with `Last-Event-ID` gets exactly the events it missed. An
investigation is tens of seconds of real measurement - a dropped connection
must never mean running the sandbox again. Frames are unnamed `message`
events, so a client cannot silently drop an event type nobody registered a
listener for.

    id: 34
    data: {"type": "phase_judged", "hypothesis": "B", "phase": "ablate",
           "state": "healthy", "p95_ms": 10.84, "local_control_ms": 8.82,
           "ratio": 1.23, "controls_agree": true, "drift": 1.0}

The frontend renders what arrives and computes nothing. `PROVEN` appears on
screen because the backend emitted `{"type": "verdict", "verdict": "PROVEN"}`,
and for no other reason.

## The demo incident

Two changes shipped to `order-service` inside the same fifteen-minute window,
both perfectly correlated with a latency regression.

| | Change | Diff | Effect |
|---|---|---|---|
| **A** | `refactor/order-query-batching` | 9 files, 412 lines | none - the decoy |
| **B** | `perf/normalise-audit-predicate` | 1 file, 3 lines | the actual cause |

B wraps `order_id` in an expression inside the audit predicate, which makes the
index on `order_audit(order_id)` unusable and turns every lookup into a full
table scan. A is a large, alarming-looking refactor that issues exactly the
same queries as the code it replaced.

Every observational signal points at A. Only an experiment separates them.

## Observational ranking, and what it is

`causeway/observational.py` ranks candidates the way an approach with only
correlational evidence must: same service, how recently it shipped, how large
the diff is, how much of the slow code path it touched. On this incident it
computes

    A  0.961    B  0.567

and confidently names the decoy. Those numbers are computed from the deploy
record by a real weighted formula, not written down - `test_observational.py`
asserts them.

**This is a stand-in for that class of reasoning, built for this controlled
demo. It is not a model of any commercial product, and no claim is made that
real tools use this arithmetic.** It gets the answer wrong for the honest
reason: a three-line change caused the outage and a 412-line change did not,
and no amount of correlational evidence can tell those two apart.

It is also blind by construction. It cannot import the sandbox, the replay,
the measurements or the verdict, and a test enforces that.

## Where the model is allowed to sit

    localizer  ->  PLANNER  ->  validator  ->  sandbox  ->  measure  ->  verdict
    (code)         (model)      (code)        (code)      (code)      (code)

A planner receives the incident, the candidates, the available interventions
and the available fixtures. It returns one `ExperimentSpec` and nothing else.
It is given no measurement, it is called before anything runs, and its output
passes eight deterministic checks before the sandbox will touch it:

| Check | Rejects |
|---|---|
| `schema` | missing or unexpected fields, a smuggled `verdict` key, a pinned absolute threshold |
| `hypothesis_in_candidates` | a change the localizer never surfaced |
| `intervention_surface_exists` | an intervention the sandbox cannot make |
| `single_independent_variable` | moving more than one flag, or a no-op |
| `fixture_exists` | a fabricated replay |
| `discriminates_between_two` | an experiment that separates nothing |
| `expected_signature_wellformed` | a metric, comparison or factor of its own choosing |
| `no_encoded_verdict` | a conclusion in any field the engine reads |

`reasoning_summary` is presentation only. It is quoted on screen and never read
by the engine; if it contains verdict language it is flagged as such, and a
test proves prose claiming "B is PROVEN" leaves the computed verdict untouched.

Any failure - no key, no network, a timeout, malformed JSON, a rejected plan -
falls through to the deterministic planner, which emits the same shape and goes
through the same validator. Every plan carries its provenance, and the
interface must show it. **Claiming AI designed an experiment the fallback
designed would be the one dishonest thing Causeway could do.**

## The Gemini planner

Gemini designs the experiments. It decides nothing.

Given the incident, the localised candidates, their correlation-only scores,
the interventions the sandbox can make and the fixtures it can replay, it
returns one `ExperimentSpec` - which change to remove, which traffic to replay,
and what it would expect to see if that change were the cause. That output then
passes the same eight deterministic checks the offline planner's output passes.
It is never weakened for the model.

    incident evidence -> Gemini -> ExperimentSpec -> validator -> sandbox
                                                  -> measurements -> verdict

**The information boundary.** `causeway/planner/gemini.py:build_prompt` is the
boundary, and tests assert on the string it produces: no phase result, no
ratio, no control, no verdict, no phase name, no millisecond figure of any
kind, and nothing that says which candidate is the real cause. `PlanRequest`
has no field that could carry one either - that is checked structurally, not
just textually. The model is told the judging *rule* (the failure counts as
present at 4x a local control and gone at 2.5x) because it needs that to
express an expectation; it is never told a measurement.

**What it cannot do.** It cannot declare a verdict: a plan carrying a `verdict`
or `confidence` key is rejected outright, and verdict language in any field the
engine reads is rejected too. `reasoning_summary` is the exception - it is
prose for a human, quoted on screen and never read by the engine, so a plan
claiming "B is PROVEN" is accepted and *flagged*, and a test proves the
computed verdict is untouched. It cannot choose a threshold, name a metric of
its own, invent an intervention, move more than one flag, or fabricate a
fixture. And it is not reachable from `causeway/verdict.py` - the import-graph
test now names `causeway.planner.gemini` explicitly.

**Which candidate gets tested is not Gemini's call.** The orchestrator runs one
experiment per localised candidate, in the localizer's deterministic order, and
asks Gemini to design each one. That is an experimental policy rather than a
narrative one: refuting A is a result in its own right, and stopping at the
first hypothesis would mean never learning that the top-ranked suspect is
innocent. Gemini designs the experiment; it does not choose the agenda.

### Three planner states, three labels

| What happened | `kind` | `used_fallback` | Shown as |
|---|---|---|---|
| Gemini proposed a plan and the validator accepted it | `gemini` | `false` | **Gemini** |
| Gemini was asked and something went wrong | `deterministic` | `true` | **Deterministic Fallback** |
| No key configured, or `--offline` | `deterministic` | `false` | **Deterministic Planner** |

A run that never had a key is a deterministic *run*, not a fallback, and
nothing in the terminal or the browser calls it one. Nothing is ever labelled
Gemini unless a Gemini plan was the one that ran.

"Something went wrong" is deliberately everything: no key, an unreachable API,
an HTTP error, rate limiting, a timeout, malformed JSON, a response that is not
a plan, a schema violation, or a plan the validator rejects. All of it lands in
the same place, the investigation completes, and the verdict is unchanged.

### Running with Gemini on Windows

The key lives in the environment and nowhere else. For the current PowerShell
session:

    $env:GEMINI_API_KEY="<your key>"

Optional:

    $env:CAUSEWAY_GEMINI_MODEL="gemini-3.6-flash"   # default
    $env:CAUSEWAY_GEMINI_TIMEOUT="20"               # seconds
    $env:CAUSEWAY_OFFLINE="1"                       # never call Gemini

Check the setup before demoing - this asks for one real plan and prints what
came back, without ever printing the key:

    cd backend
    python -m causeway.cli gemini-check

If the model name is wrong it lists the ones the key can actually use. Then run
the investigation as usual; the planner is chosen automatically:

    python -m causeway.api            # or: python -m causeway.cli investigate

`python -m causeway.cli investigate --offline` forces the deterministic planner
for a run where nothing may touch the network. The key is read only by
`causeway/planner/gemini.py`, travels in a request header rather than a URL, is
redacted from every error message, and never enters an event, the SSE stream or
the browser. `.env` is gitignored.

## The controlled experiment

Each hypothesis runs seven phases. Every phase that carries evidence has a
healthy control measured immediately before it and immediately after it:

| Phase | State | Expectation |
|---|---|---|
| `control-1` | every candidate off | what healthy costs, right now |
| `reproduce` | incident state | failure present |
| `control-2` | every candidate off | healthy again |
| `ablate` | one candidate removed, everything else fixed | failure absent |
| `control-3` | every candidate off | the ablation is now bracketed both sides |
| `restore` | the candidate put back | failure returns |
| `control-4` | every candidate off | healthy one last time |

Every judgement is a ratio against **the median of the two controls beside that
phase**. The failure counts as present at `>= 4x` its local control and gone at
`<= 2.5x`, with a 5 ms noise floor applied in both directions.

| condition | verdict |
|---|---|
| failure reproduced, removal recovers, restoring brings it back | `PROVEN` |
| the failure survives the removal | `REFUTED` |
| removal recovers but the recurrence cannot be established | `SUPPORTED` |
| the incident never reproduced, the ablation landed between recovery and failure, or the controls beside a phase disagree by more than 3x | `UNRESOLVED` |

### Why the controls are interleaved

Measuring one control at the start of a run and one at the end sounds
sufficient and is not. A run long enough to reproduce an incident three times
is long enough for a laptop to genuinely change speed - thermal limits, a
scanner waking, the page cache filling. A start-to-end guard charges all of
that drift against every phase at once and abstains on perfectly good
experiments.

Interleaving makes drift local. The machine is allowed to move over the run;
what it may not do is move *between a phase and the controls either side of
it*. `test_verdict.py` pins a run whose controls drift 4.8x end to end and
whose verdict is still `PROVEN`, and asserts every verdict is unchanged when
each measurement is scaled from 0.05x to 100x.

### Why nothing is measured only once

p95 over a few dozen requests is a tail statistic - at n=40 it is the
second-slowest request in the replay. One antivirus scan lands squarely in that
tail. Every phase is therefore replayed three times and its number is the
median of those repetitions. That makes the *estimator* robust; it does not
change what a measurement has to clear, so it cannot influence a verdict.

## Sizing, not hardcoding

`python -m causeway.cli seed` measures this machine and sizes the audit table
so the incident lands around 14x healthy - unmistakable, and fast enough that a
full investigation finishes while someone is watching. How many rows that takes
depends on the disk, the cache and the antivirus, so it is measured rather than
inherited from somebody else's laptop.

Nothing recorded at seed time reaches a verdict. Those numbers are setup
diagnostics; every experiment measures its own controls while it runs.

## Scope boundary

The intervention is real: exactly one variable moves, every other flag is held
fixed, the workload is byte-identical between phases, and the database is
restored between them. What is simulated is the *deployment mechanism* - a
candidate change is a runtime flag rather than a rebuild from a reverted
commit. Worth saying out loud rather than letting someone find it.

## Layout

    backend/
      causeway/
        verdict.py          THE VERDICT - no model may be reachable from here
        measurement.py      p50/p95, and the median across repetitions
        incident.py         the incident and the deploy record (data)
        localizer.py        deterministic candidate filtering
        observational.py    the correlation-only baseline - structurally blind
        planner/
          schema.py         ExperimentSpec and the JSON schema
          validator.py      the eight deterministic checks
          deterministic.py  the offline planner, and the fallback for every
                            possible Gemini failure
          gemini.py         Gemini over REST - proposes, never decides
        sandbox/
          seed.py           deterministic database builder + calibration
          service.py        the demo order-service (its own process)
          replay.py         deterministic fixture replay
          runner.py         lifecycle: restore, set flags, replay, repeat
        orchestrator.py     the investigation, as a stream of events
        cli.py              milestone 1 entry point
      tests/
      fixtures/             recorded traffic (portable, in git)
      .data/                this machine's database and calibration (not in git)
