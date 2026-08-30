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

## Status

The causal core, a live investigation streamed to a browser, the dashboard
that explains it, a verified fix loop, and a repository investigation that
reads hypotheses out of a real repository's own source and settles them by
editing that source in disposable copies.

| | |
|---|---|
| Milestone 1 | causal core, CLI-verified · **done** |
| Milestone 2 | API + SSE + frontend shell · **done** |
| Milestone 3 | the investigation dashboard · **done** |
| Milestone 4 | Gemini plans the experiments · **done** |
| Milestone 5 | Gemini plans a fix, verified in a disposable sandbox copy · **done** |
| Milestone 6 | GitHub repository ingestion, narrowly scoped · **done** |
| Milestone 7 | the repository path becomes the real path: source-read hypotheses, source-edit experiments, repository-owned database, user intent · **done** |
| Milestone 8 | live telemetry → deterministic risk detection → confirmed incident → automatic handoff into the same causal investigation · **done** |

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
    npm test                             # vitest run - graph.ts and nothing else yet

Then one process, one URL - this is the demo-day path:

    cd ..\backend
    python -m causeway.api               # http://127.0.0.1:8000

While iterating, two processes instead (`run-dev.ps1` starts both):

    cd backend  && python -m causeway.api
    cd frontend && npm run dev           # http://127.0.0.1:5173, proxies /api

The command line still works and needs no dependencies at all:

    python -m causeway.cli investigate   # the full investigation, in the terminal
    python -m causeway.cli events        # the same run as raw NDJSON

    python -m causeway.cli investigate --repository-url https://github.com/<owner>/<repo>
                                          # investigate a repository instead of the bundled demo

## The API

| | |
|---|---|
| `GET /api/health` | is the backend up, is this machine seeded, what thresholds is the engine using |
| `GET /api/status` | what the current investigation is doing |
| `POST /api/investigation` | start one. `202` with a run id, or `409` naming the run already in progress |
| `GET /api/investigation/stream` | Server-Sent Events, resumable |
| `GET /api/investigation/{id}/events` | the whole buffer as JSON |
| `GET /api/investigation/{id}/graph` | the causal graph, built deterministically from that same buffer - see [The causal graph](#the-causal-graph) |
| `POST /api/telemetry` | ingest one real telemetry sample for one service |
| `GET /api/prediction/status` | the engine's current risk assessment(s), as last computed - optionally `?service=` |
| `GET /api/prediction/system` | the system-wide risk rollup across every service with telemetry - see [System risk](#system-risk) |
| `POST /api/services/register` | link a service name to the GitHub repository its incidents should hand off to |
| `GET /api/services` | the currently registered service → repository links |
| `GET /api/monitor/stream` | Server-Sent Events - telemetry, risk, and incident events, independent of any one investigation |

See [Live monitoring](#live-monitoring--telemetry-prediction-and-incident-handoff) below for what each of
the five monitoring endpoints actually does and does not decide.

`POST /api/investigation` takes an optional JSON body. With no body at all it
runs the bundled demonstration, exactly as before these fields existed:

    {
      "repository_url": "https://github.com/owner/repo",
      "instruction":    "find why the audit endpoint is slow, do not modify anything",
      "mode":           "diagnose_only"
    }

`instruction` is carried verbatim to `causeway.intent` and is the only thing
that reads it — the frontend never parses it and never picks a mode on the
user's behalf. `mode` is optional; when given it overrides whatever the words
suggest, because the user chose it. An unknown mode is a `400`, not a
reinterpretation: guessing at a mode is how a run that was told to change
nothing ends up changing something.

Seeding is the bundled demonstration's precondition, not the product's — it
builds Causeway's own template database. A repository brings its own schema
and seed, so an unseeded machine can still investigate a repository.

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
    $env:CAUSEWAY_GEMINI_TIMEOUT="20"               # seconds - the experiment and fix planners
    $env:CAUSEWAY_GEMINI_PATCH_TIMEOUT_SECONDS="90" # seconds - the patch planner, its own variable
    $env:CAUSEWAY_OFFLINE="1"                       # never call Gemini

`CAUSEWAY_GEMINI_PATCH_TIMEOUT_SECONDS` is deliberately separate from
`CAUSEWAY_GEMINI_TIMEOUT`. `causeway/patch/gemini.py` (the requested-change
and standard-repository patch planner) can carry a real repository's bounded
source context - tens of files, tens of thousands of characters - where the
experiment and fix planners' requests are small and structured; 90s gives it
real headroom without silently widening the other two. It is clamped to
`[5, 300]` seconds regardless of what is set, so a mistyped or malicious
value can neither starve a request nor let one hang indefinitely. A patch
request that still times out is reported cleanly - "AI patch generation
timed out before a safe patch could be produced. No repository files were
changed." - never with the internal detail of why a narrow offline fallback
also declined; nothing is retried and nothing is applied.

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

## The fix loop

Once a hypothesis is deterministically `PROVEN` - never before, and never for
one that is only `SUPPORTED` or `REFUTED` - Causeway asks a second, narrower
question: what should the broken code become?

    root_cause_proven -> Gemini FixSpec -> deterministic fix validator ->
    sandbox fix application (disposable copy) -> identical workload replay ->
    deterministic measurements -> deterministic fix verdict

Gemini sees the proven hypothesis, the causal mechanism, and the current
(broken) value at one named, whitelisted repair surface - never a
fix-verification measurement, because none exists yet, and never the
known-safe answer, which lives only in a module Gemini's code path cannot
reach. It returns a `FixSpec`: which surface, and what it should become. The
same nine-check deterministic validator every proposal goes through rejects
anything that targets an unregistered surface, doesn't match the sandbox's
real current value, isn't the one known-safe repair, or smuggles a verdict
into a field the engine reads.

A validated fix is applied only to a **disposable copy** of the service code
- never the checked-in source - and that copy is launched as its own
subprocess. The same incident workload is then replayed through a five-phase
protocol (`fix-control-1, fix-before, fix-control-2, fix-after,
fix-control-3`) that reproduces the failure on the unpatched service and
retests it on the patched one, judged by the identical local-control
arithmetic the causal verdict uses. `causeway/fix_verdict.py` is the only
place `VERIFIED` / `FAILED` / `UNRESOLVED` is decided, and
`test_no_model_in_fix_verdict.py` proves nothing model-shaped, networked, or
subprocess-shaped is reachable from it - the same structural guarantee
`causeway/verdict.py` has for the causal decision.

**Nothing is ever deployed.** A verified fix is shown for human review; it is
never pushed, committed, merged, or applied to any file Causeway did not
create disposably for the run.

## Repository investigation — the real path

Causeway can investigate a GitHub repository. This is not the bundled A/B
demonstration pointed at a URL: on this path there is no A and no B, no
fabricated deploy history, and no correlation ranking. Causeway clones the
repository, builds a database from **the repository's own schema and seed**,
**reads the repository's own source** for suspicious locations, and settles
between them by editing source in disposable copies and measuring.

    GitHub URL + your instruction
      -> validate -> clone (disposable, isolated workspace)
      -> causeway.json v2 validated
      -> database built from the repository's own schema and seed
      -> hypotheses READ OUT OF THE REPOSITORY'S SOURCE by deterministic detectors
      -> an experiment per hypothesis, performed as a SOURCE EDIT in a
         disposable copy
      -> the same seven-phase protocol, the same verdict engine
      -> (only if your instruction allows it) a fix, validated, applied to
         another disposable copy, and verified

The two paths share exactly one thing: `causeway/verdict.py` and
`causeway/fix_verdict.py`. They share nothing else, and that is checked
rather than asserted — `backend/tests/test_repo_path_isolation.py` walks
`causeway.repo_investigation`'s import graph and fails the build if
`causeway.incident`, `causeway.localizer` or `causeway.observational` ever
becomes reachable from it, or if anything on that path so much as names
Causeway's own seeded `TEMPLATE_DB`.

### What this is, stated honestly

**Causeway does not autonomously debug arbitrary repositories, and it does
not generate arbitrary code from natural language.** Two narrow things are
true instead, and both are demonstrable:

- **The hypotheses are real, and Causeway finds them itself.** They are not
  declared in a manifest — a manifest that tries to declare them is rejected
  by name. They come from `causeway/analysis/detectors.py`, which learns
  which columns are indexed from the repository's own `schema.sql`, walks
  the Python AST for SQL string literals, and reports predicates that wrap an
  indexed column so the index cannot be used. That is **one detector**, for
  one class of defect. A repository containing no pattern it recognises is
  told so — never investigated as something else.
- **The fix is a substitution at one proven location, not generated code.**
  A fix planner selects a whitelisted repair surface; the bytes written are
  the repository's own text and the counterfactual the detector derived from
  the repository's own schema. `operation.type` may only be
  `replace_predicate`. A model cannot introduce a new string into a file, and
  cannot name a file at all.

What is genuinely general is the *method*: the seven-phase interleaved
protocol, the local controls, the abstention rules, and the refusal to let
anything but a measurement decide. Widening the detector set widens what
Causeway can investigate without touching any of that.

### Your instruction, and what it controls

Causeway takes an instruction in your own words, and a mode you can state
explicitly in the interface. The instruction is the goal: it is quoted, never
rewritten, and never replaced by what a model would have preferred.

| mode | what it permits |
|---|---|
| `diagnose_only` | experiments run; **no persistent fix is planned, proposed or applied** |
| `diagnose_and_fix` | a fix may be proposed for a PROVEN cause, and must verify before it is claimed |
| `requested_change` | Gemini proposes a real patch for the change you described; validated, applied to a disposable copy, and checked (see below) |
| `needs_clarification` | the instruction was ambiguous — Causeway asks, and clones nothing |

A diagnostic intervention is not a fix. An experiment edits a disposable copy
to establish causality and throws the copy away; a fix is a change you are
being asked to keep. `diagnose_only` permits the first and forbids the second,
and it is a gate rather than a label — the fix planner is never asked.

Constraints are split into two kinds, and shown as two different things:

- **Enforced** — checked in deterministic code before anything is written:
  `only_modify`, `do_not_modify`, `diagnose_only`, `no_new_dependencies`,
  `no_schema_change`, `max_changed_files`.
- **Advisory** — recorded and displayed, not mechanically checked ("keep it
  simple", "preserve backward compatibility"). Claiming to have enforced one
  of these would be the same class of dishonesty as labelling a fallback
  "Gemini".

If no instruction is given at all, the run defaults to `diagnose_only` and
says so — absence of an instruction is not ambiguity, and the safe reading of
"investigate this repository" is to change nothing.

### Supported URL format

Exactly `https://github.com/<owner>/<repo>`, with an optional trailing
`.git`. Rejected: any other scheme (`http://`, `file://`, `javascript:`),
any host other than `github.com`, credentials embedded in the URL, a port, a
path outside `<owner>/<repo>`, and path traversal in any form — the
validator is an allow-list against GitHub's own owner/repo naming rules, not
a denylist of things to reject.

### The repository contract: `causeway.json` version 2

A manifest declares **capabilities and safe inputs**. It may not declare the
answer. Version 1 could — it carried `deploys` and a `repair_surface` — which
is exactly why version 1 is no longer accepted.

    {
      "version": 2,
      "service": "order-service",
      "runtime": "python",
      "entrypoint": "app.py",
      "sources":   ["app.py", "db.py"],
      "patchable": ["db.py"],
      "workload": "workload.json",
      "verification": "latency_p95",
      "incident": { "id": "...", "title": "...", "service": "...",
                    "symptom": "...", "detected_at": "..." },
      "database": {
        "engine": "sqlite",
        "schema": "schema.sql",
        "seed": [ { "table": "order_audit", "rows": 40000,
                    "columns": { "order_id": {"kind": "cycle", "modulo": 5000} } } ]
      }
    }

A manifest carrying any of `repair_surface`, `root_cause`, `deploys`,
`answer`, `correct_hypothesis`, `known_cause`, `verdict` or `fix` is
**rejected by name**, before anything else is checked:

    a manifest describes capabilities, not conclusions - remove deploys,
    repair_surface. Causeway finds hypotheses by reading the source and
    settles them by measuring.

There are no command strings anywhere in this vocabulary. Causeway runs
`python <entrypoint>` and nothing else. The schema may only create and drop
tables, indexes and views — `ATTACH`, `PRAGMA`, `load_extension`, `INSERT`
and `DELETE` are all refused, before a database file exists. Seed columns
come from a closed set of kinds (`rowid`, `cycle`, `choice`, `text`, `const`,
`int_range`) with a hard row cap. Every path in the manifest is resolved and
re-checked to be inside the cloned workspace; one that escapes it, by any
spelling, is rejected rather than sanitised.

### How an experiment is performed

A hypothesis on this path is a place in real source, so the intervention is
an edit to real source:

    healthy       every testable location replaced by its counterfactual
    incident      the repository exactly as cloned, nothing applied
    ablated:<id>  as cloned, with exactly ONE location's counterfactual

Each phase copies the cloned workspace, applies its edits to the **copy**,
launches `python <entrypoint>` against the copy, measures, and deletes the
copy — whether the phase succeeded or raised. Every edit path is resolved and
re-checked **after** `realpath`, so a symlink cannot walk out of the
workspace, and each edit's target text must occur exactly once in the file:
an ambiguous match means the caller does not know which occurrence it is
changing, and a causal experiment cannot be built on that.

Measured cost of that actuator on the reference machine: about **0.35 s per
phase** — copy, start, health-check and cleanup — against a seven-phase
experiment. The clone is never written to. Causeway's own checkout is never
written to. The repository on GitHub is never pushed to, committed to or
merged into.

**Causeway never silently substitutes the bundled demonstration.** A
repository URL either produces a real investigation of that repository —
its own database, its own source, its own workload — or a visible rejection.

### Current limitations

- **One detector**: `sql_predicate_index_usability`. Wrapped indexed columns
  in SQL predicates. Nothing else is detected, and nothing else is claimed.
- **One runtime** (`python`, standard library only), **one storage engine**
  (`sqlite`), **one verification metric** (`latency_p95`), and **one repair
  operation type** (`replace_predicate`).
- **Public repositories only.** No OAuth, no personal access token, no GitHub
  App — a private repository fails cleanly rather than prompting for
  credentials.
- **At least two testable hypotheses are required.** An experiment that
  cannot discriminate between anything is not run.
- **Sandbox-only fix verification.** A verified fix is shown for human
  review. Nothing is pushed, committed, merged or deployed — to the
  repository investigated or anywhere else.
- **`requested_change` is recognised, not implemented.** Causeway records the
  request and diagnoses; it does not write the feature.
- **One investigation at a time** — the sandbox is a real process measuring
  real latency, and two runs sharing a machine would measure each other.

### The demo repository

`demo-repo/` in this checkout is a complete, working example of the contract:
a small order-service with its own schema, its own 40,000-row seed, its own
workload, and **two statically identical suspects**. Both wrap an indexed
column in a predicate. One is on a 40,000-row audit table and is the
incident; the other is on a six-row lookup table and costs nothing. No
detector can tell them apart, because statically they are the same. The
experiment settles it — and that is the whole product in one screen.

## Standard repository analysis — causeway.json is optional

`causeway.json` is required for exactly one thing: the controlled causal
experiment described above, which needs a repeatable workload and a database
built from the repository's own schema to measure anything against. It is
**not** required to read a repository, propose a change, and check it — a
normal public repository that never heard of Causeway's contract still gets
a real investigation, not a rejection.

    GitHub URL + your instruction
      -> validate -> clone (disposable, isolated workspace)
      -> causeway.json present?  yes -> the causal-experiment path above
                                 no  -> language(s) detected
                                        (causeway.languages - file signals
                                        only, nothing is ever executed to
                                        detect it)
      -> a bounded, instruction-scored selection of the repository's own
         source (never the whole tree)
      -> Gemini proposes a CodePatch (or the narrow deterministic fallback
         does, for the one instruction shape it recognises)
      -> the same deterministic causeway.patch.validator every requested
         change goes through: relative paths, no traversal, resolved and
         proven inside the workspace, must be both analysable and declared
         patchable, never .git/.env/anything credential-shaped, before-text
         must match the file exactly as it stands, bounded file/hunk counts,
         every enforceable constraint from your instruction
      -> applied to a disposable copy - never the clone
      -> whatever CHEAP, NON-EXECUTING check that file's own language can
         safely run
      -> VERIFIED, FAILED, or IMPLEMENTED — VERIFICATION INCOMPLETE

**Causeway's standard repository analysis uses a language-adapter
architecture. It currently recognizes Python, JavaScript, TypeScript, Java,
Go, C and C++ repositories (C# and Rust are detected but not yet verified).
Gemini proposes source-level changes from bounded repository context, while
deterministic code validates and applies patches only in a disposable
workspace. Full behavioral verification depends on the repository exposing a
trusted runnable verification surface.**

That last sentence is the honest limit of this path, stated plainly: a
compile or syntax check is not proof a program behaves correctly, and this
path never claims it is. `causeway/standard_investigation.py` reports
`VERIFIED` **never** — only a `causeway.json` repository's real HTTP probes
(`causeway/requested_change.py`) can earn that word, because only that path
has something to run the patched program against. A standard repository's
patch that passes its language's check is `IMPLEMENTED_VERIFICATION_
INCOMPLETE`; one that fails it is `FAILED`; nothing here upgrades the first
to the second on Gemini's own say-so.

### Language adapters (`causeway/languages/`)

One small adapter per language, added without touching the walking, scoring,
bounding or patch-application logic anywhere else:

| Language | Detected by | Safe verification |
|---|---|---|
| Python | `.py`, `requirements.txt`, `pyproject.toml`, `setup.py` | `py_compile` (parses and compiles to bytecode; never runs the module) |
| JavaScript | `.js`/`.jsx`/`.mjs`/`.cjs`, `package.json` | `node --check` (parses only) |
| TypeScript | `.ts`/`.tsx`, `tsconfig.json` | `tsc --noEmit`, only if a compiler is already present in the repository's own `node_modules` or on the machine's `PATH` — Causeway never installs one |
| Java | `.java`, `pom.xml`/`build.gradle`(`.kts`) | `javac` against changed files whose imports are standard-library only; a file that imports anything else is reported unavailable, never guessed at |
| Go | `.go`, `go.mod` | `go vet -mod=vendor`, only when the repository already vendors its dependencies — `go build`/`vet` otherwise resolves a module graph over the network, which this path does not do |
| C | `.c`/`.h`, `Makefile`/`CMakeLists.txt` | `gcc -fsyntax-only` (no codegen, nothing linked or run) |
| C++ | `.cpp`/`.cc`/`.cxx`/`.hpp`/`.hh`/`.hxx`, `Makefile`/`CMakeLists.txt` | `g++ -fsyntax-only` |
| C# *(detection only)* | `.cs`, `*.csproj`/`*.sln` | unavailable — `dotnet build` needs `dotnet restore` first, which fetches NuGet packages over the network |
| Rust *(detection only)* | `.rs`, `Cargo.toml` | `cargo check --offline`, only when the repository already vendors its crates |

Detection uses multiple signals, never only an extension, and a repository
may be — and often is — more than one language at once: `language_detected`
reports a `primary` and every other language actually found, weighted by a
project marker at the repository root (a strong signal) plus how many
matching source files exist (a weaker one). A repository is rejected only
when **no** adapter's signal is present at all, and the rejection names what
Causeway does recognise rather than mentioning `causeway.json`.

Every adapter's `verify` follows one rule without exception: it may run a
compiler or interpreter's own syntax/type-check flag — never install a
dependency, download a package, run a repository-provided script, or execute
the program the repository defines. The moment that is not possible, it
reports `available: False` and says why, rather than reaching for something
riskier to produce an answer. `tests/test_languages.py` includes a static
audit of every argv array an adapter can build, proving none of them is an
install or fetch subcommand, plus a full run against a real, deliberately
broken JavaScript repository through the actual orchestrator (mocked Gemini,
real `node --check`) end to end.

### What is shared with the causeway.json path, unchanged

`causeway/patch/` — the `CodePatch` model, the Gemini planner, and the
deterministic validator — is the same code both `causeway/requested_change.py`
(a manifest repository's requested change) and `causeway/standard_investigation.py`
(a repository with none) call. Widening language support never touched path
safety, the denylist, or constraint enforcement; `causeway.languages.registry.
is_denied_path` is the one function both source selection (a `.env` file is
never even read into a prompt) and the patch validator (a patch may never
touch one, regardless of what a planner was offered) call, so the two can
never quietly disagree about what "denied" means.

### Current limitations, stated plainly

- **Compiling is not running.** Every standard-path check is a syntax or
  type check. None of them proves the patched program behaves correctly at
  runtime — that is what `IMPLEMENTED_VERIFICATION_INCOMPLETE` means, and
  why this path never reports `VERIFIED`.
- **A missing toolchain is not a failure.** Go, Rust, C#, and TypeScript
  without a locally-available compiler all report `available: False` rather
  than attempting to install one — the same honesty `py_compile`'s absence
  would get if Python itself were somehow missing.
- **Bounded context.** At most 12 files and 40,000 characters are shown to a
  planner, scored by your instruction and by how central a file looks
  (entrypoint names, root-level location) — a very large or deeply nested
  repository may not surface the single most relevant file first.
- **No arbitrary-language support.** A repository in a language with no
  adapter here is rejected, honestly, for that — never silently treated as
  one of the ones above.

## Live monitoring — telemetry, prediction, and incident handoff

Everything above starts from a human typing a repository URL and an
instruction. Milestone 8 adds a second front door: a running service that
posts its own real telemetry, a deterministic engine that watches that
telemetry for sustained movement toward a known failure condition, and — only
once that movement is *confirmed*, not glimpsed — an incident that hands
itself to the same causal investigation described above, evidence attached.

    LIVE APPLICATION → LIVE TELEMETRY → DETERMINISTIC RISK DETECTION →
    CONFIRMED INCIDENT → RUNTIME EVIDENCE + REPOSITORY CONTEXT →
    CAUSEWAY CAUSAL DEBUGGER (unchanged from everything above)

Causeway monitors telemetry from a running service and looks for sustained
movement toward known failure conditions. When a risk becomes significant, it
captures the runtime evidence and starts a causal investigation against the
service's repository. Gemini proposes testable hypotheses and source changes,
but deterministic validators, sandbox experiments, and measured recovery
determine whether the cause and fix are verified.

Nothing about the causal core changes to make this work. A telemetry-triggered
investigation runs through `repo_investigation.py`, the same detectors, the
same seven-phase experiment, the same verdict module nothing model-shaped can
reach. What's new sits entirely upstream of it, deciding *when* to start one
and *with what evidence* — never deciding a root cause or a verdict itself.

### Telemetry is measured, not modelled

`POST /api/telemetry` accepts one JSON sample per call:

    {
      "service": "order-service-pool",
      "timestamp": "2026-08-30T12:00:04Z",
      "cpu_percent": 41.2, "memory_percent": 58.0,
      "request_rate": 12.5, "p50_ms": 38.0, "p95_ms": 210.0, "p99_ms": 480.0,
      "error_rate": 0.02,
      "db_pool_used": 9, "db_pool_capacity": 12, "db_waiting_requests": 3,
      "db_query_p95_ms": 61.0,
      "rate_limit_429_rate": 0.0, "rate_limit_remaining": 200
    }

`causeway/telemetry/schema.py` validates every field before it is stored:
unknown fields, `NaN`/`inf`, out-of-range values, and booleans posing as
numbers are all rejected with `400` — Causeway never guesses a metric that
wasn't actually reported, and it never fabricates a sample to fill a gap.
`causeway/telemetry/store.py` keeps a bounded, per-service rolling window in
memory (`MAX_SAMPLES_PER_SERVICE = 240`); there is no telemetry database and
none is implied.

### Detection is deterministic, and it is a risk score, not a prediction of doom

`causeway/prediction/` holds three purpose-built detectors, each a plain
function over the recent window — no model, no training, no Gemini call on
this path at all:

| detector | watches for |
|---|---|
| `connection_pool_exhaustion` | pool utilisation rising, requests starting to wait, query latency rising with it |
| `memory_pressure` | memory rising over time, independent of request volume |
| `latency_degradation` | p95 latency and error rate both moving away from their own baseline |

Each produces a `RiskAssessment`: a level (`LOW`/`MEDIUM`/`HIGH`), a plain-text
evidence list built from the actual numbers (`"pool 55→96 (util 96%)"`, not a
paraphrase), and — only when the trend supports it — an ETA in seconds to the
threshold, computed from an OLS slope over real samples
(`causeway/prediction/trends.py`), never asserted without one.

A single elevated sample is never enough. `causeway/prediction/engine.py`
requires `CONFIRM_AFTER = 3` **consecutive** raw-HIGH evaluations before a risk
is `confirmed`, and `RECOVER_AFTER = 3` consecutive non-HIGH evaluations
before it is considered to have recovered — this is the hysteresis that keeps
one noisy sample from opening an incident, and keeps a recovered service from
immediately reopening one. The engine also evaluates each service against a
bounded recent window (`RECENT_WINDOW = 20` samples), not its entire history,
so a second incident episode is judged on its own trend rather than one
contaminated by the first.

### An incident is created only on confirmation, and only once per episode

`causeway/incidents.py` is edge-triggered: it watches for the transition from
*not confirmed* to *confirmed*, not the state itself, so a risk that stays
HIGH for fifty consecutive samples opens exactly one incident, not fifty. When
one opens, and the service has been linked to a repository
(`POST /api/services/register`), the incident hands off automatically into a
real investigation — the same `run_starter` any manual repository run uses —
carrying the confirmed risk's evidence into the instruction Gemini receives.
An unlinked service's incident is created with
`status: AWAITING_REPOSITORY_CONTEXT` and waits; it is never silently
discarded, and it is never investigated against a repository nobody linked to
it.

`POST /api/services/register` runs the same `causeway.repository.validate_url`
allow-list the manual investigation form uses — registering a service is not
a lighter-weight way to point Causeway at a repository the real investigation
path would have refused.

### System risk

`causeway/prediction/rollup.py` answers one further question none of the
per-service, per-detector assessments above answer on their own: **is the
system, as a whole, at risk right now, and how many services are degrading**.
It is a pure aggregation over the same `RiskAssessment`s the engine already
produced — no new detection, no new score, nothing decided here that a
detector or the hysteresis engine had not already decided — mapped onto five
states:

| State | Meaning |
|---|---|
| `STABLE` | every detector's own level is `LOW` |
| `WATCH` | a detector's own level is `MEDIUM` |
| `ELEVATED` | a detector's own level is `HIGH`, not yet confirmed |
| `HIGH_RISK` | a detector's own level is `HIGH`, confirmed by sustained evidence |
| `INSUFFICIENT_DATA` | no detector has produced an assessment at all |

A service with no assessments is `INSUFFICIENT_DATA`, never folded into
`STABLE` — "nothing has said this is a problem yet" and "every detector
looked and found nothing" are different claims, and only the second one is
`STABLE`. The system's own state is the most severe state among every
service it knows about; its score is the most severe service's own score,
scaled to 0–100.

Two implementations of this one rollup exist, the same way the causal graph
has two: `frontend/src/systemRisk.ts`'s `buildSystemRisk` computes it
instantly, client-side, from the exact `MonitorState` `MonitorPanel` already
folds from `/api/monitor/stream` — live, with no network round trip, and
never blank. `GET /api/prediction/system` is the backend's own answer, built
server-side from `causeway.prediction.engine`'s live evaluation of every
service the telemetry store has samples for. `SystemRiskPanel` renders the
client-side rollup immediately, then fetches the backend's version in the
background (debounced on the monitor event count, not polling) and switches
to it once it lands, tagging the panel `BACKEND-VERIFIED`; a failed fetch
degrades silently back to the live view.

### Watching it happen

`GET /api/monitor/stream` is a second, independent SSE stream (the frontend's
`useMonitor` hook, mirroring `useInvestigation`'s render-only discipline) that
carries five event types as they happen: `telemetry_received`,
`risk_updated`, `failure_predicted` (emitted only once a risk reaches HIGH),
`incident_created`, and `investigation_handoff`. The `MonitorPanel` component
renders exactly what arrives — the risk pill, its evidence lines and its ETA
are all copied from the event's own fields, never recomputed. Clicking a
linked incident's "view investigation" attaches the existing
`useInvestigation` stream to the run the handoff already started, so the rest
of the page — hypotheses, experiments, verdicts — is the identical component
tree the manual repository path renders.

To see it live: `python -m causeway.cli telemetry-demo` drives real HTTP load
against a small bundled fixture service (`demo-repo-pool/`, a connection pool
sized to exhaust under sustained load) and posts its *actually observed*
metrics — nothing simulated — to `/api/telemetry` every two seconds. Left
running, utilisation climbs, waiting requests appear, p95 and the error rate
follow, the engine confirms `connection_pool_exhaustion`, an incident opens,
and — if the service was registered against a repository — an investigation
starts on its own.

### What this is not

This is a hackathon prototype, not a guarantee of predicting every production
outage. The three bundled detectors cover three specific, well-understood
failure shapes; a service degrading in a way none of them model produces no
risk assessment at all, and Causeway says nothing rather than inventing a
guess. There is no machine learning here and no plan to add one on this path —
the detectors are the same kind of deterministic, inspectable arithmetic as
the rest of the causal core. Gemini is never called by the prediction path
itself; it is only reached once a human (or a confirmed incident's automatic
handoff) starts an actual investigation. And nothing on this path ever
deploys, restarts, or scales anything in production — it observes, scores,
and, at most, opens an investigation against a disposable sandbox copy of the
repository, exactly as every other path in this README does.

**Extensibility, stated rather than built**: telemetry arrives today as one
JSON `POST` per sample, which is deliberately the smallest possible surface —
adding a receiver that unpacks an OpenTelemetry OTLP metrics payload into the
same `validate_sample()` call is a translation layer at the edge, not a
change to detection, hysteresis, or incident logic. That translation layer is
out of scope for this milestone and was not built; the schema it would feed
already exists.

## The causal graph

The graph has two implementations of one contract, on purpose, not two
products: `causeway/graph.py`'s `build_graph()` on the backend, and
`frontend/src/graph.ts`'s `buildCausalGraph()` on the frontend. Both are
pure functions of an investigation's own event buffer (the identical
sequence `GET /api/investigation/{run_id}/events` already returns), both
decide nothing `causeway.verdict` did not already decide, and both produce
the same node/edge shape — a node or an edge built by one is indistinguishable
from one built by the other, and `GraphDrawer.tsx` renders either without
knowing which it got.

`GET /api/investigation/{run_id}/graph` is the backend's own answer, built
server-side from that run's event buffer plus (for the one `prediction` case
below) `causeway.incidents.manager.all()`. `CausalGraph.tsx` renders
instantly from its own client-side `buildCausalGraph(state, monitor)` — the
same `InvestigationState` every other panel on the page already folds from
SSE, so the graph is live with zero network latency and is never blank — and
then fetches the backend's version in the background, debounced on the
event count so a burst of measurement events collapses into one request
rather than one per event. Once that request lands the page switches to
rendering the backend's own graph and shows a `BACKEND-VERIFIED` tag; if the
request fails, the client-side graph keeps rendering exactly as it already
was — `BACKEND_UNAVAILABLE` degrades silently into the live view rather than
a blank panel. This is not polling: nothing here runs on a timer, a fetch is
only ever triggered by the event buffer actually having grown.

**Nodes**: `incident`, `repository`, `candidate` (a bundled-demo deploy),
`code_change` (a repository hypothesis — file, line, symbol, the exact text
found there), `experiment`, `fix`, and `prediction`. Every field on a node
came from a field on an already-received event; none is computed or guessed
by either implementation.

**Edges, and the causal truth model**: a suspected cause is wired straight to
the incident labelled "suspected cause" and drawn dashed. The moment its
hypothesis starts, an `experiment` node is spliced in between them and the
edge into the incident is relabelled from the experiment's own
`causeway.verdict` outcome — `PROVEN` becomes "verified causal relationship"
(solid), `REFUTED` becomes "refuted", `SUPPORTED` and `UNRESOLVED` keep their
own words. The graph never uses causal language ahead of a verdict, and never
upgrades one verdict's label into a stronger one. A `prediction` node appears
only when `causeway.incidents.manager`'s own handoff already tied a risk
episode to this run (`Incident.run_id` matching the investigation's `run_id`)
— never inferred from two nodes merely naming the same service.

**Interaction**: clicking a node opens a detail drawer built from that same
node's `metadata` — for a `code_change` node this is genuinely the observed
source line and the counterfactual a detector derived, never a fabricated one;
for an `experiment` node it separates the measured phase numbers (observed),
the planner's `reasoning_summary` (AI interpretation, explicitly labelled and
never read by the verdict engine), and `verdict.reason()`'s own sentence
(verified conclusion). A `fix` node's diff is labelled an AI-proposed patch,
distinct from the repository's own code.

**Tests**: `backend/tests/test_graph.py` (`python -m unittest tests.test_graph`)
and `backend/tests/test_api.py`'s `GraphEndpointTests` cover the server-side
builder and its route; `frontend/src/graph.test.ts` and `graphApi.test.ts`
(`npm test` in `frontend/`) cover the client-side builder and the fetch
wrapper. Both builder test suites assert the same set of cases: an empty
graph, an incident with nothing else yet, a not-yet-tested candidate staying
a candidate, the experiment splice and its per-verdict label
(`PROVEN`/`REFUTED`), a repository code hypothesis wired through its
repository node, a fix node attaching to its experiment, a fix node never
appearing unproposed, and the prediction link appearing only for a matching
`run_id` and never for an unrelated one.

**Current limitations, stated plainly**: this is a graph adapter over the
existing event stream, not a new investigation capability — it shows nothing
the event buffer did not already carry. There is no trace or log node type
because the backend does not emit traces or logs; only file/line source
evidence and measured latencies exist today, so those are what the graph
shows. Layout (`dagre`, top-to-bottom) is computed client-side only — the
backend endpoint returns nodes and edges, not pixel positions — with no
manual repositioning, grouping, or minimap yet.

## The bundled demonstration

Running with no repository URL runs the bundled A/B demonstration: two
fabricated deploy records, a deterministic localizer, a correlation-only
ranking that picks the decoy, and runtime flags standing in for a
deployment. It is a demonstration of the method on a system built to
demonstrate it, and both the interface and this README say so. It exists
because it is fast, deterministic and shows the correlation-versus-
intervention contrast in one screen — not because it is the product.

## Sizing, not hardcoding

`python -m causeway.cli seed` measures this machine and sizes the audit table
so the incident lands around 14x healthy - unmistakable, and fast enough that a
full investigation finishes while someone is watching. How many rows that takes
depends on the disk, the cache and the antivirus, so it is measured rather than
inherited from somebody else's laptop.

Nothing recorded at seed time reaches a verdict. Those numbers are setup
diagnostics; every experiment measures its own controls while it runs.

## Scope boundary

On **both** paths the intervention is real: exactly one variable moves, every
other one is held fixed, the workload is byte-identical between phases, and
the database is restored between them.

What differs is what a variable *is*.

- **Repository path**: a variable is a location in that repository's own
  source, and moving it means writing different bytes into a disposable copy
  and launching it. Nothing about the deployment mechanism is simulated —
  what is narrow is the *class of defect* Causeway can currently detect.
- **Bundled demonstration**: a variable is a runtime flag rather than a
  rebuild from a reverted commit. The deployment mechanism is simulated
  there, and that is worth saying out loud rather than letting someone find
  it.

## Layout

    backend/
      causeway/
        verdict.py          THE CAUSAL VERDICT - no model may be reachable from here
        fix_verdict.py      THE FIX VERDICT - same structural guarantee, five phases
        measurement.py      p50/p95, and the median across repetitions
        repo_investigation.py  THE REPOSITORY PATH - cannot reach the three below
        phasing.py          shared phase-to-event rendering (verdict's words only)
        incident.py         the bundled demonstration's incident record (data)
        localizer.py        deterministic candidate filtering (bundled only)
        observational.py    the correlation-only baseline (bundled only)
        analysis/
          hypothesis.py     CodeHypothesis - a file, a line, a counterfactual
          detectors.py      hypotheses read out of real source; no manifest input
          detectors_pool.py resource-release-not-guaranteed - AST acquire/release pairs
        intent/
          schema.py         IntentSpec, enforceable vs advisory constraints
          deterministic.py  the offline instruction reader
        planner/
          schema.py         ExperimentSpec and the JSON schema
          validator.py      the eight deterministic checks
          deterministic.py  the offline planner, and the fallback for every
                            possible Gemini failure
          gemini.py         Gemini over REST - proposes, never decides
        fixer/
          schema.py         FixSpec and the JSON schema
          validator.py      the nine deterministic fix checks
          deterministic.py  the offline fix planner and fallback
          gemini.py         Gemini proposes a fix - never verifies one
        repository/
          urlcheck.py       GitHub URL validation - an allow-list, not a denylist
          git.py            safe, argument-array clone into a disposable workspace
          manifest.py       causeway.json v2 - capabilities only, never answers
          database.py       the repository's OWN database, from a declarative
                            contract; no command strings anywhere in it
          standard.py       the manifest-less path: language detection dispatch,
                            bounded scored source selection - no manifest required
        languages/          one adapter per supported language - detection
                            signals and one safe, non-executing verification path
          base.py           the LanguageAdapter contract
          registry.py       every adapter, the one detection pass, shared
                            SKIP_DIRS and the credential/secret path denylist
          adapters.py       Python, JavaScript, TypeScript, Java, Go, C, C++,
                            C#, Rust
        patch/              the CodePatch model + deterministic validator shared
                            by causeway.requested_change AND
                            causeway.standard_investigation
        telemetry/
          schema.py         validate_sample() - the only way a sample enters Causeway
          store.py          bounded, per-service, thread-safe rolling window
        prediction/
          schema.py         RiskAssessment - level, evidence, ETA; no verdict field
          base.py           the Detector contract every detector implements
          trends.py         slope/median/persistence/ETA - pure functions, no model
          connection_pool.py / memory_pressure.py / latency_degradation.py
          registry.py       every detector, in the order they are evaluated
          engine.py         hysteresis: CONFIRM_AFTER / RECOVER_AFTER, bounded window
        incidents.py        edge-triggered incident creation + investigation handoff
        services.py         service name → repository link, same URL allow-list
        monitor.py          the live-monitoring SSE stream, independent of any run
        production.py       wires telemetry → prediction → incidents → monitor feed
        demo/
          production_service.py  real HTTP load against demo-repo-pool, real
                            telemetry posted from what was actually observed
        standard_investigation.py  a repository with no causeway.json: propose,
                            validate, apply to a disposable copy, verify with
                            whatever that file's language can safely check
        requested_change.py  a causeway.json repository's requested change:
                            the same patch machinery, verified against the
                            manifest's own declared HTTP probes instead
        sandbox/
          seed.py           deterministic database builder + calibration
          service.py        the bundled demo order-service (its own process)
          replay.py         deterministic fixture replay
          runner.py         lifecycle: restore, set flags, replay, repeat
          repair.py         whitelisted repair surfaces (bundled + repository)
          fixapply.py       patches a disposable copy - never the checked-in source
          variant.py        disposable SOURCE VARIANTS - the repository actuator
          actuator.py       how a phase's state is put into effect: flags, or edits
        orchestrator.py     the investigation, as a stream of events
        api.py               the HTTP surface
        cli.py               the command-line entry point
      tests/
      fixtures/             the bundled demo's recorded traffic (portable, in git)
      .data/                this machine's database and calibration (not in git)
    demo-repo/              a real repository with two identical-looking suspects
    demo-repo-pool/         a real, reproducible connection-pool-exhaustion bug,
                            fed by causeway.demo.production_service for the
                            live telemetry → prediction → incident demo
