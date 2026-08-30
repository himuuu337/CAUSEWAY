"""Causeway command line - the Milestone 1 entry point.

    python -m causeway.cli seed          size the sandbox to this machine
    python -m causeway.cli investigate   run the full investigation
    python -m causeway.cli events        the same run as raw NDJSON

`events` emits exactly what the API will stream to the browser, so the
interface can be built against real output from the start.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time

from causeway import config, fix_verdict, intent, verdict
from causeway.sandbox import seed as seedmod
from causeway.sandbox.replay import build_fixture, save_fixture
from causeway.orchestrator import investigate

WIDTH = 78


def _supports_colour() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        return bool(kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7))
    except Exception:
        return False


class Style:
    def __init__(self, enabled: bool):
        self.enabled = bool(enabled) and _supports_colour()

    def _paint(self, code, text):
        return "\033[%sm%s\033[0m" % (code, text) if self.enabled else text

    def bold(self, t): return self._paint("1", t)
    def dim(self, t): return self._paint("2", t)
    def green(self, t): return self._paint("32", t)
    def red(self, t): return self._paint("31", t)
    def yellow(self, t): return self._paint("33", t)
    def cyan(self, t): return self._paint("36", t)


def _rule(title=""):
    if title:
        print("\n" + title)
    print(" " + "-" * (WIDTH - 1))


def _wrap(text, indent="   "):
    for line in textwrap.wrap(text, width=WIDTH - len(indent)):
        print(indent + line)


# ---------------------------------------------------------------------- seed

def cmd_seed(args) -> int:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.FIXTURE_DIR, exist_ok=True)
    print("Causeway setup - sizing the sandbox to this machine")
    _rule()
    print("  The incident needs to be unmistakable and the investigation needs")
    print("  to finish while somebody is watching. How many audit rows that")
    print("  takes depends on this disk, this cache and this antivirus, so it")
    print("  is measured here rather than assumed.")
    print()

    def on_round(record):
        print("  round %d  %7d rows (%.1f MB)   healthy %7.2f ms   "
              "incident %8.2f ms   %5.1fx  %s"
              % (record["round"], record["audit_rows"], record["bytes"] / 1e6,
                 record["healthy_p95_ms"], record["incident_p95_ms"],
                 record["ratio"], "accepted" if record["accepted"] else "retrying"))

    result = seedmod.calibrate(config.TEMPLATE_DB, config.WORK_DB,
                               repetitions=config.repetitions(2),
                               on_round=on_round)
    print()
    if not result["separable"]:
        print("SETUP FAILED: this machine cannot separate the healthy and incident",
              file=sys.stderr)
        print("  states - incident p95 %.2f ms is only %.1fx healthy %.2f ms, and the"
              % (result["incident_p95_ms"], result["ratio"], result["healthy_p95_ms"]),
              file=sys.stderr)
        print("  engine needs %.1fx. Most likely CAUSEWAY_DATA is on slow or network"
              % verdict.FAILURE_FACTOR, file=sys.stderr)
        print("  storage - point it at a local disk and seed again.", file=sys.stderr)
        return 1

    fixture = build_fixture(result["orders"])
    save_fixture(config.FIXTURE_PATH, fixture)
    with open(config.CALIBRATION_PATH, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")

    print("  sized:    %d audit rows over %d orders (%.1f MB)"
          % (result["audit_rows"], result["orders"], result["bytes"] / 1e6))
    print("  healthy:  p95 %8.2f ms" % result["healthy_p95_ms"])
    print("  incident: p95 %8.2f ms   (%.1fx healthy)"
          % (result["incident_p95_ms"], result["ratio"]))
    print("  fixture:  %d requests, concurrency %d"
          % (len(fixture["requests"]), fixture["concurrency"]))
    print()
    _wrap("These numbers are setup diagnostics. No number recorded here ever "
          "reaches a verdict - every experiment measures its own controls "
          "while it runs.", "  ")
    return 0


# --------------------------------------------------------------- investigate

_VERDICT_COLOUR = {verdict.PROVEN: "green", verdict.REFUTED: "red",
                   verdict.SUPPORTED: "cyan", verdict.UNRESOLVED: "yellow"}

_FIX_VERDICT_COLOUR = {fix_verdict.VERIFIED: "green", fix_verdict.FAILED: "red",
                       fix_verdict.UNRESOLVED: "yellow"}

_REQUESTED_CHANGE_COLOUR = {"VERIFIED": "green", "FAILED": "red", "UNRESOLVED": "yellow",
                            "IMPLEMENTED_VERIFICATION_INCOMPLETE": "yellow"}

_STATE_MARK = {"broken": "BROKEN", "healthy": "HEALTHY",
               "inconclusive": "INCONCLUSIVE", "unstable": "UNSTABLE"}


def cmd_investigate(args) -> int:
    style = Style(enabled=not args.no_color)
    offline = getattr(args, "offline", False) or None
    repository_url = getattr(args, "repository_url", None)
    instruction = getattr(args, "instruction", None)
    mode = getattr(args, "mode", None)
    judged = {}
    status = 0

    # Step numbers are assigned in the order the stages actually happen, not
    # hardcoded: the repository path has no observational ranking, and a
    # printout that jumped from [1] to [3] would look like a missing stage
    # rather than a stage that does not exist on that path.
    steps = {}

    def step(name: str) -> int:
        return steps.setdefault(name, len(steps) + 1)

    for event in investigate(offline=offline, repository_url=repository_url,
                             instruction=instruction, mode=mode):
        kind = event["type"]

        if kind == "error":
            print(style.red(event["message"]), file=sys.stderr)
            return 2

        if kind == "intent":
            _rule(style.bold(" WHAT YOU ASKED FOR")
                  + style.dim("   read by %s" % event["source"]))
            if event["raw_instruction"]:
                _wrap('"%s"' % event["raw_instruction"], "   ")
            print("   mode            %s" % style.bold(event["mode"].upper()))
            print("   persistent fix  %s"
                  % (style.green("allowed") if event["allows_fix"]
                     else style.dim(event["no_fix_reason"] or "not permitted")))
            for constraint in event["enforced"]:
                print("   enforced        %s %s"
                      % (style.bold(constraint["kind"]), constraint["value"]))
            for constraint in event["advisory"]:
                print(style.dim("   advisory        %s (recorded, not checked)"
                                % constraint["value"]))

        elif kind == "needs_clarification":
            print(style.yellow(style.bold("\n   CAUSEWAY NEEDS ONE ANSWER")))
            _wrap(event["question"], "   ")
            _wrap("Nothing was cloned and nothing was measured. Re-run with "
                  "--mode " + " | --mode ".join(event["modes"]), "   ")
            return 3

        elif kind == "repository_validating":
            print(style.dim(" validating repository URL: %s" % event["url"]))

        elif kind == "repository_cloning":
            print(style.dim(" cloning %s/%s ..." % (event["owner"], event["name"])))

        elif kind == "repository_loaded":
            print("=" * WIDTH)
            print(style.bold(" REPOSITORY   %s/%s" % (event["owner"], event["name"])))
            print(" commit %s   service %s   runtime %s"
                 % (event["commit_sha"][:12], event["service"], event["runtime"]))
            print(style.green("   supported Causeway project"))
            print(style.dim("   runs %s %s   analyses %s   patchable %s"
                            % (event["runtime"], event["entrypoint"], ", ".join(event["sources"]),
                               ", ".join(event["patchable"]))))
            if event["database"] is not None:
                tables = ", ".join("%s %s rows" % (name, "{:,}".format(rows))
                                   for name, rows in sorted(event["database"]["tables"].items()))
                print(style.dim("   database built from this repository's own schema: %s"
                                % tables))
            else:
                print(style.dim("   no causeway.json manifest - standard repository "
                                "analysis, no database built"))

        elif kind == "repository_rejected":
            print(style.red(" UNSUPPORTED REPOSITORY (%s): %s"
                            % (event["stage"], event["reason"])), file=sys.stderr)
            print(style.dim(" this repository does not contain a supported Causeway "
                            "demo configuration"), file=sys.stderr)
            return 2

        elif kind == "requested_change_start":
            _rule(style.bold(" [%d] SOURCE READ" % step("source_read"))
                  + style.dim("   no manifest - every mode funnels through here"))
            print("   %d file%s considered: %s"
                  % (len(event["files_considered"]),
                     "" if len(event["files_considered"]) == 1 else "s",
                     ", ".join(event["files_considered"]) or "(none)"))

        elif kind == "language_detected":
            counts = ", ".join("%s %d" % (lang, n) for lang, n in event["counts"].items())
            print("   language        %s   detected %s"
                  % (style.bold(event["primary"] or "(none)"), counts or "(none)"))

        elif kind == "source_selection":
            print("   selected        %s" % (", ".join(event["files"]) or "(none)"))
            print(style.dim("   entrypoint      %s   tests %s"
                            % (event["entrypoint"] or "(none recognisable)",
                               "found - " + event["tests_note"] if event["tests_detected"]
                               else "none found - " + event["tests_note"])))

        elif kind == "patch_rejected":
            print("   " + style.yellow(style.bold("NO PATCH APPLIED")))
            _wrap(event["reason"], "     ")

        elif kind == "patch_plan":
            prov = event["provenance"]
            if prov["used_fallback"]:
                label = "DETERMINISTIC FALLBACK"
            elif prov["kind"] == "gemini":
                label = "GEMINI (%s)" % prov["source"].replace("gemini:", "")
            else:
                label = "DETERMINISTIC PLANNER"
            patch = event["patch"]
            print("   patch designed by %s" % style.cyan(style.bold(label)))
            print("   summary         %s" % patch["summary"])
            print("   files           %s" % ", ".join(f["path"] for f in patch["files"]))

        elif kind == "patch_validation":
            mark = style.green(style.bold("%d/%d PASSED" % (event["passed"], event["total"])))
            print("   patch validator %s" % mark)
            for check in event["checks"]:
                tick = style.green("ok") if check["passed"] else style.red("NO")
                print(style.dim("     %s  %-31s %s"
                                % (tick, check["name"], check["detail"][:36])))

        elif kind == "patch_apply":
            print(style.green("   PATCH APPLIED") + style.dim("  %s" % event["applied_to"]))
            print("   summary         %s" % event["summary"])
            print("   files           %s" % ", ".join(event["files"]))

        elif kind == "verification_check":
            tick = style.green("ok") if event["passed"] else style.red("NO")
            print(style.dim("     %s  [%s] %-24s %s"
                            % (tick, event["language"], event["name"], event["detail"][:36])))

        elif kind == "requested_change_verdict":
            paint = getattr(style, _REQUESTED_CHANGE_COLOUR.get(event["verdict"], "yellow"))
            print("   %s  %s" % (style.dim("VERDICT"), style.bold(paint(event["verdict"]))))
            _wrap(event["reason"], "     ")
            if event["verdict"] != "VERIFIED":
                status = 3

        elif kind == "done":
            print(style.dim("   completed in %.1fs" % event["elapsed_s"]))
            print("=" * WIDTH)

        elif kind == "incident":
            incident = event["incident"]
            cal = event.get("calibration")
            print("=" * WIDTH)
            print(style.bold(" CAUSEWAY   %s   %s"
                             % (incident["id"], incident["service"])))
            print(" %s   %s" % (incident["title"], incident["symptom"]))
            if cal:
                # the bundled demonstration was calibrated at seed time
                print(style.dim(" healthy ~%.0f ms    incident ~%.0f ms    %.0fx    "
                                "detected %s"
                                % (cal["healthy_p95_ms"], cal["incident_p95_ms"],
                                   cal["ratio"], incident["detected_at"])))
            else:
                # a repository has no calibration, and none is invented
                print(style.dim(" detected %s   nothing measured yet"
                                % incident["detected_at"]))
            print("=" * WIDTH)
            replay = event.get("fixture") or event.get("workload")
            print(style.dim(" replay %s: %d requests, concurrency %d, %d repetitions "
                            "per phase" % (replay["id"], replay["requests"],
                                           replay["concurrency"],
                                           event["repetitions"])))

        elif kind == "hypotheses":
            _rule(style.bold(" [%d] SOURCE ANALYSIS" % step("analysis"))
                  + style.dim("   deterministic detectors, no model"))
            print("   %d location%s found in %s; %d testable."
                  % (len(event["hypotheses"]),
                     "" if len(event["hypotheses"]) == 1 else "s",
                     ", ".join(event["sources"]), len(event["testable"])))
            for h in event["hypotheses"]:
                print("     %s   %s"
                      % (style.bold(h["label"]), style.red(h["observed"])))
                if h["counterfactual"]:
                    print(style.dim("       would test    %s" % h["counterfactual"]))
                _wrap(h["reason"], "       ")
            _wrap("These are the same shape as one another. Static analysis cannot "
                  "say which is causal - that is what the experiments are for.",
                  "   ")

        elif kind == "candidates":
            _rule(style.bold(" [%d] CANDIDATE LOCALISATION" % step("localization"))
                  + style.dim("   deterministic, no model"))
            print("   %d deploys in the record; %d survive the service and window "
                  "filters." % (event["deploys_considered"], len(event["candidates"])))
            for c in event["candidates"]:
                print("     %s  %-34s %s  %2d file%s %4d lines"
                      % (style.bold(c["change_id"]), c["branch"], c["sha"],
                         c["files_changed"], " " if c["files_changed"] == 1 else "s",
                         c["lines_changed"]))
            for e in event["excluded"]:
                print(style.dim("     %s  %-34s %s"
                                % (e["change_id"], e["branch"], e["reason"])))

        elif kind == "observational":
            _rule(style.bold(" [%d] OBSERVATIONAL RANKING" % step("observational"))
                  + style.dim("   correlation only, no experiment"))
            for rank, a in enumerate(event["assessments"], start=1):
                print("   #%d  %s  %-34s score %s"
                      % (rank, style.bold(a["change_id"]), a["branch"],
                         style.bold("%.3f" % a["score"])))
                comp = a["components"]
                print(style.dim("       service %.2f  recency %.2f  magnitude %.2f  "
                                "hot-path %.2f"
                                % (comp["same_service"], comp["recency"],
                                   comp["magnitude"], comp["hot_path_overlap"])))
            print()
            print("   " + style.yellow(style.bold("TOP OBSERVATIONAL SUSPECT: %s"
                                                  % event["top_suspect"])))
            _wrap("This is the change a correlation-only view points at. It is a "
                  "stand-in for that class of reasoning, not a model of any "
                  "particular product.", "   ")

        elif kind == "plan":
            prov, plan = event["provenance"], event["plan"]
            _rule(style.bold(" [%d] EXPERIMENT PLAN  %s" % (step("planning"), event["hypothesis"]))
                  + style.dim("   proposes only, never decides"))
            # three states, three labels. A run that never had a key is a
            # deterministic RUN, not a fallback, and must not be called one.
            if prov["used_fallback"]:
                label = "DETERMINISTIC FALLBACK"
            elif prov["kind"] == "gemini":
                label = "GEMINI (%s)" % prov["source"].replace("gemini:", "")
            else:
                label = "DETERMINISTIC PLANNER"
            print("   designed by     %s" % style.cyan(style.bold(label)))
            if prov["used_fallback"]:
                print(style.dim("                   fell back from %s - %s"
                                % (prov["proposed_by"], prov["fallback_reason"])))
            print("   intervention    set %s = %s, hold every other flag fixed"
                  % (plan["intervention"]["flag"],
                     "on" if plan["intervention"]["value"] else "off"))
            print("   fixture         %s" % plan["fixture_id"])
            sig = plan["expected_signature"]
            print("   expects         %s %s %.1fx the control measured beside the phase"
                  % (sig["metric"], sig["op"], sig["factor"]))
            print(style.dim("   reasoning       (quoted only - never read by the engine)"))
            _wrap('"%s"' % plan["reasoning_summary"], "     ")

        elif kind == "validation":
            passed, total = event["passed"], event["total"]
            mark = style.green(style.bold("%d/%d PASSED" % (passed, total)))
            print("   validator       %s" % mark)
            for check in event["checks"]:
                tick = style.green("ok") if check["passed"] else style.red("NO")
                print(style.dim("     %s  %-31s %s"
                                % (tick, check["name"], check["detail"][:36])))
            if event["reasoning_flagged"]:
                print(style.dim("     note: the reasoning mentions a result - quoted, "
                                "never read"))

        elif kind == "experiment_start":
            _rule(style.bold(" [%d] CONTROLLED EXPERIMENT  %s" % (step("experiment"), event["hypothesis"]))
                  + style.dim("   measurements decide"))
            if event.get("label"):
                # a repository: the intervention is an edit to real source
                print(style.dim("   %s   %s -> %s"
                                % (event["label"], event["observed"],
                                   event["counterfactual"])))
                print(style.dim("   applied to a disposable copy; a control is "
                                "measured either side of every phase"))
            else:
                print(style.dim("   moving %s, holding %s fixed; a control is measured "
                                "either side of every phase"
                                % (event["intervention"]["flag"],
                                   ", ".join(event["holding_fixed"]) or "nothing")))
            judged.clear()

        elif kind == "phase_result":
            # measured now; an evidence phase is judged once the control on its
            # far side has also been measured, which is the next event but one
            print("     %-10s %9.2f ms%s"
                  % (event["phase"], event["p95_ms"],
                     style.dim("   control") if event["role"] == "control" else ""))

        elif kind == "phase_judged":
            judged[event["phase"]] = event
            paint = style.green if event["state"] == "healthy" else (
                style.red if event["state"] == "broken" else style.yellow)
            print(style.dim("       -> %s") % event["phase"]
                  + "  %s" % paint(style.bold(_STATE_MARK.get(event["state"], "?")))
                  + style.dim("  %.1fx its local control of %.2f ms"
                              % (event["ratio"], event["local_control_ms"])))

        elif kind == "verdict":
            decision = event["verdict"]
            paint = getattr(style, _VERDICT_COLOUR[decision])
            print("     %s %s  %s" % (style.dim("VERDICT"),
                                      style.bold(event["hypothesis"]),
                                      style.bold(paint(decision))))
            _wrap(event["reason"], "        ")

        elif kind == "fix_skipped":
            _rule(style.bold(" NO FIX GENERATED")
                  + style.dim("   %s" % event["mode"]))
            _wrap(event["reason"] + ". The experiments above edited source only "
                  "inside disposable copies - a diagnostic intervention, not a "
                  "change anyone is being asked to keep.", "   ")

        elif kind == "fix_blocked":
            print("   " + style.yellow(style.bold("NO FIX PROPOSED")))
            _wrap("%s (%s scope)" % (event["reason"], event["scope"]), "     ")

        elif kind == "conclusion":
            _rule(style.bold(" [%d] CONTRAST" % step("conclusion")))
            if event.get("observational_top_suspect"):
                print("   observational ranking put %s first."
                      % style.bold(event["observational_top_suspect"]))
            for change_id, decision in event["verdicts"].items():
                paint = getattr(style, _VERDICT_COLOUR[decision])
                print("   experiment      %s  %s"
                      % (style.bold(change_id), style.bold(paint(decision))))
            print()
            proven = event.get("proven_labels") or event["proven"]
            if event.get("correlation_selected_decoy"):
                print("   " + style.red("Correlation selected the decoy."))
                print("   " + style.green(
                    "Controlled intervention identified the causal change: %s."
                    % ", ".join(proven)))
            elif proven and event.get("observational_top_suspect"):
                print("   " + style.green("Correlation and intervention agree: %s."
                                          % ", ".join(proven)))
            elif proven:
                print("   " + style.red("Static analysis could not tell these apart."))
                print("   " + style.green("The experiment did: %s." % ", ".join(proven)))
            else:
                print("   " + style.yellow("Nothing survived its experiment. "
                                           "Nothing is claimed."))
                status = 3
            print(style.dim("   completed in %.1fs" % event["elapsed_s"]))
            print("=" * WIDTH)

        elif kind == "root_cause_proven":
            _rule(style.bold(" [%d] VERIFIED FIX LOOP  %s" % (step("fix"), event["hypothesis"]))
                  + style.dim("   only for a PROVEN cause"))

        elif kind == "fix_plan":
            prov, fix = event["provenance"], event["fix"]
            if prov["used_fallback"]:
                label = "DETERMINISTIC FALLBACK"
            elif prov["kind"] == "gemini":
                label = "GEMINI (%s)" % prov["source"].replace("gemini:", "")
            else:
                label = "DETERMINISTIC PLANNER"
            print("   fix designed by %s" % style.cyan(style.bold(label)))
            print("   summary         %s" % fix["summary"])
            op = fix["operation"]
            print("   change          %s: %r -> %r" % (op["target"], op["before"], op["after"]))

        elif kind == "fix_validation":
            mark = style.green(style.bold("%d/%d PASSED" % (event["passed"], event["total"])))
            print("   fix validator   %s" % mark)

        elif kind == "fix_apply":
            print(style.dim("   applying %s to a disposable sandbox copy - the "
                            "real source tree is never touched" % event["hypothesis"]))

        elif kind == "fix_phase_result":
            print("     %-14s %9.2f ms%s"
                  % (event["phase"], event["p95_ms"],
                     style.dim("   control") if event["role"] == "control" else ""))

        elif kind == "fix_phase_judged":
            paint = style.green if event["state"] == "healthy" else (
                style.red if event["state"] == "broken" else style.yellow)
            print(style.dim("       -> %s") % event["phase"]
                  + "  %s" % paint(style.bold(_STATE_MARK.get(event["state"], "?")))
                  + style.dim("  %.1fx its local control of %.2f ms"
                              % (event["ratio"], event["local_control_ms"])))

        elif kind == "fix_verdict":
            paint = getattr(style, _FIX_VERDICT_COLOUR.get(event["verdict"], "yellow"))
            print("     %s %s  %s" % (style.dim("FIX VERDICT"),
                                      style.bold(event["hypothesis"]),
                                      style.bold(paint(event["verdict"]))))
            _wrap(event["reason"], "        ")
            print(style.dim("   verified in sandbox only - human review required "
                            "before any real deployment"))

    return status


def cmd_events(args) -> int:
    offline = getattr(args, "offline", False) or None
    repository_url = getattr(args, "repository_url", None)
    for event in investigate(offline=offline, repository_url=repository_url,
                             instruction=getattr(args, "instruction", None),
                             mode=getattr(args, "mode", None)):
        print(json.dumps(event), flush=True)
        if event["type"] in ("error", "repository_rejected"):
            return 2
    return 0


def cmd_gemini_check(args) -> int:
    """Setup diagnostics for the planner. Never part of an investigation.

    Prints whether a key is configured, which model is selected, which models
    the key can actually reach, and whether one real round trip produces a plan
    the validator accepts. It never prints the key.
    """
    from causeway import observational, planner
    from causeway.incident import deploy_record
    from causeway.localizer import localize
    from causeway.planner.gemini import GeminiPlanner
    from causeway.planner.schema import ProviderUnavailable

    style = Style(enabled=not args.no_color)
    provider = GeminiPlanner()
    print("Causeway - Gemini planner check")
    _rule()
    print("  GEMINI_API_KEY   %s"
          % (style.green("configured") if provider.available
             else style.red("not set - the deterministic planner will be used")))
    print("  model            %s" % provider.model)
    print("  timeout          %.0fs" % provider.timeout)
    if config.offline():
        print("  " + style.yellow("CAUSEWAY_OFFLINE is set - investigations will "
                                  "not call Gemini at all"))
    if not provider.available:
        print()
        print("  Set a key for this PowerShell session with:")
        print("    $env:GEMINI_API_KEY=\"<your key>\"")
        return 1

    print()
    print("  models this key can use for generateContent:")
    try:
        names = provider.list_models()
        for name in names[:14]:
            marker = " <- selected" if name == provider.model else ""
            print("    %s%s" % (name, style.green(marker)))
        if provider.model not in names:
            print("  " + style.yellow("  %s is not in that list - set "
                                      "CAUSEWAY_GEMINI_MODEL to one that is"
                                      % provider.model))
    except ProviderUnavailable as exc:
        print("    " + style.red("could not list models: %s" % exc))

    print()
    print("  asking Gemini for one experiment plan ...")
    record = deploy_record()
    candidates, _ = localize(record)
    state = {candidate.change_id: True for candidate in candidates}
    request = planner.build_request(
        record["incident"], candidates, state, [config.FIXTURE_ID], "B",
        observational=observational.rank(candidates, record["incident"]))
    outcome = planner.plan_experiment(request, provider)
    prov = outcome.as_dict()["provenance"]

    if prov["kind"] == "gemini":
        print("  " + style.green(style.bold("ACCEPTED")) + "  planned by %s"
              % prov["source"])
        print("    intervention   set %s = %s"
              % (outcome.plan.intervention["flag"],
                 "on" if outcome.plan.intervention["value"] else "off"))
        _wrap('"%s"' % outcome.plan.reasoning_summary, "    ")
        return 0

    print("  " + style.yellow(style.bold("FELL BACK")) + " to the deterministic "
          "planner")
    _wrap(prov["fallback_reason"], "    ")
    print(style.dim("    The demo still works - this is the path that keeps it "
                    "working when Gemini is not."))
    return 1


# ---------------------------------------------------------- telemetry demo

def cmd_telemetry_demo(args) -> int:
    """Runs a real order-service with a real connection-pool bug, drives
    real load against it, and posts what actually happened to Causeway's
    /api/telemetry - the live counterpart to `investigate`: this produces
    the incident an investigation is run against, rather than running one
    itself. Requires `python -m causeway.api` (or `causeway.cli investigate`
    is not enough here) already running and reachable at --causeway-url."""
    from causeway.demo import production_service

    style = Style(enabled=not args.no_color)
    print("Causeway - live telemetry demo")
    _rule()
    print("  starting order-service-pool: a real HTTP service with a real")
    print("  connection-pool bug (causeway.analysis.detectors_pool finds it")
    print("  in demo-repo-pool/app.py, the same source this service runs)")
    print("  reporting real telemetry to %s every %.0fs"
          % (args.causeway_url, production_service.TELEMETRY_INTERVAL_SECONDS))
    print("  Ctrl-C to stop")
    print()

    started_at = time.time()

    def on_sample(sample, posted):
        used, capacity = sample.get("db_pool_used"), sample.get("db_pool_capacity")
        utilisation = (used / capacity * 100.0) if used is not None and capacity else None
        line = ("  t+%6.1fs  req/s=%5.1f  p95=%7.1fms  err=%5.1f%%"
               % (time.time() - started_at, sample.get("request_rate", 0.0),
                  sample.get("p95_ms", 0.0), sample.get("error_rate", 0.0) * 100.0))
        if utilisation is not None:
            line += ("  pool=%5.1f%%  waiting=%3d"
                    % (utilisation, sample.get("db_waiting_requests", 0)))
        line += "  " + (style.green("posted") if posted
                        else style.red("POST FAILED - is `python -m causeway.api` running?"))
        print(line)

    try:
        production_service.run(causeway_url=args.causeway_url, service_name=args.service,
                               duration_seconds=args.duration, on_sample=on_sample)
    except KeyboardInterrupt:
        print()
        print("  stopped")
    except RuntimeError as exc:
        print(style.red("  %s" % exc), file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------- monitor a repository

def cmd_monitor_repository(args) -> int:
    """Clones a real, user-provided causeway.json repository, runs its own
    entrypoint against its own database, drives its own declared workload,
    and posts what actually happened to Causeway's /api/telemetry - the
    same real-measurement discipline telemetry-demo applies to the bundled
    demo-repo-pool, for a repository the user names instead. Refuses
    plainly, before cloning finishes anything, for a repository with no
    causeway.json - there is no declared workload or entrypoint to run one
    against, the same reason causeway.cli investigate's standard path never
    executes a manifest-less repository either. Requires
    `python -m causeway.api` already running and reachable at
    --causeway-url, and (separately) `POST /api/services/register` -  or
    the Prediction panel's own "LINK REPOSITORY" form - to link --service to
    this same repository, if a confirmed risk should hand off into a real
    investigation of it."""
    from causeway import repository_monitor

    style = Style(enabled=not args.no_color)
    print("Causeway - live repository monitor")
    _rule()
    print("  repository: %s" % args.repository_url)
    print("  service:    %s" % args.service)
    print("  reporting real telemetry to %s every %.0fs"
          % (args.causeway_url, repository_monitor.TELEMETRY_INTERVAL_SECONDS))
    print("  this is a best-effort subprocess sandbox, not OS-level isolation -")
    print("  only run it against a repository you trust")
    print("  Ctrl-C to stop")
    print()

    started_at = time.time()

    def on_event(event):
        etype = event.get("type")
        if etype == "repository_validating":
            print("  validating %s ..." % event.get("url"))
        elif etype == "repository_cloning":
            print("  cloning %s/%s ..." % (event.get("owner"), event.get("name")))
        elif etype == "repository_loaded":
            print("  loaded: entrypoint=%s" % event.get("entrypoint"))
        elif etype == "repository_rejected":
            print(style.red("  rejected at %s: %s" % (event.get("stage"), event.get("reason"))))

    def on_sample(sample, posted):
        used, capacity = sample.get("db_pool_used"), sample.get("db_pool_capacity")
        utilisation = (used / capacity * 100.0) if used is not None and capacity else None
        line = ("  t+%6.1fs  req/s=%5.1f  p95=%7.1fms  err=%5.1f%%"
               % (time.time() - started_at, sample.get("request_rate", 0.0),
                  sample.get("p95_ms", 0.0), sample.get("error_rate", 0.0) * 100.0))
        if utilisation is not None:
            line += ("  pool=%5.1f%%  waiting=%3d"
                    % (utilisation, sample.get("db_waiting_requests", 0)))
        line += "  " + (style.green("posted") if posted
                        else style.red("POST FAILED - is `python -m causeway.api` running?"))
        print(line)

    try:
        repository_monitor.run(
            repository_url=args.repository_url, service_name=args.service,
            instruction=args.instruction or "", causeway_url=args.causeway_url,
            duration_seconds=args.duration, on_sample=on_sample, on_event=on_event)
    except KeyboardInterrupt:
        print()
        print("  stopped")
    except RuntimeError as exc:
        print(style.red("  %s" % exc), file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="causeway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="size the sandbox database to this machine")

    run = sub.add_parser("investigate", help="run the full causal investigation")
    run.add_argument("--no-color", action="store_true")
    run.add_argument("--offline", action="store_true",
                     help="force the deterministic planner, never call Gemini")
    run.add_argument("--repository-url", default=None,
                     help="investigate a GitHub repository (https://github.com/<owner>/<repo>) "
                          "instead of the bundled demonstration")
    run.add_argument("--instruction", default=None,
                     help="what Causeway should do with the repository, in your own "
                          "words - e.g. \"find why it is slow, do not modify anything\"")
    run.add_argument("--mode", default=None, choices=list(intent.MODES),
                     help="state the mode explicitly instead of letting the "
                          "instruction be read for one")

    events = sub.add_parser("events", help="the same run as raw NDJSON events")
    events.add_argument("--offline", action="store_true")
    events.add_argument("--repository-url", default=None)
    events.add_argument("--instruction", default=None)
    events.add_argument("--mode", default=None, choices=list(intent.MODES))

    check = sub.add_parser("gemini-check",
                           help="is the Gemini planner configured and working")
    check.add_argument("--no-color", action="store_true")

    demo = sub.add_parser(
        "telemetry-demo",
        help="run a real service with a real connection-pool bug and report "
            "real telemetry to a running Causeway backend")
    demo.add_argument("--no-color", action="store_true")
    demo.add_argument("--causeway-url", default="http://127.0.0.1:8000",
                      help="where python -m causeway.api is listening")
    demo.add_argument("--service", default="order-service-pool")
    demo.add_argument("--duration", type=float, default=None,
                      help="seconds to run before stopping automatically "
                          "(default: run until Ctrl-C)")

    monitor = sub.add_parser(
        "monitor-repository",
        help="clone a causeway.json repository, run its own entrypoint against its "
            "own database, and report real telemetry to a running Causeway backend")
    monitor.add_argument("--no-color", action="store_true")
    monitor.add_argument("--causeway-url", default="http://127.0.0.1:8000",
                         help="where python -m causeway.api is listening")
    monitor.add_argument("--repository-url", required=True,
                         help="https://github.com/<owner>/<repo> - must declare "
                             "causeway.json (an entrypoint, a workload, a database "
                             "built from its own schema)")
    monitor.add_argument("--service", required=True,
                         help="the service name to post telemetry under - link it to "
                             "this repository with POST /api/services/register (or "
                             "the Prediction panel's own form) for a confirmed risk "
                             "to hand off into a real investigation")
    monitor.add_argument("--instruction", default=None,
                         help="carried into causeway.intent the same way a manual "
                             "investigation's instruction is - optional, this path "
                             "never proposes a fix")
    monitor.add_argument("--duration", type=float, default=None,
                         help="seconds to run before stopping automatically "
                             "(default: run until Ctrl-C)")

    args = parser.parse_args(argv)
    return {"seed": cmd_seed, "investigate": cmd_investigate,
            "events": cmd_events, "gemini-check": cmd_gemini_check,
            "telemetry-demo": cmd_telemetry_demo,
            "monitor-repository": cmd_monitor_repository}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
