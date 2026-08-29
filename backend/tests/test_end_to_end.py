"""The whole loop, for real: build a database, replay traffic, intervene,
measure, decide.

Slow by design. This is the test that proves the causal claim is produced by
measurement rather than by assertion - everything else in the suite tests the
reasoning about numbers, and this one produces the numbers.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from causeway import fix_verdict, measurement, observational, verdict  # noqa: E402
from causeway.incident import deploy_record                       # noqa: E402
from causeway.localizer import localize                           # noqa: E402
from causeway.sandbox import seed, service                        # noqa: E402
from causeway.sandbox.replay import build_fixture, save_fixture   # noqa: E402
from causeway.sandbox.runner import Sandbox                       # noqa: E402


def _hash(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()

INCIDENT_STATE = {"A": True, "B": True}
AUDIT_ROWS = 40_000
# Two repetitions rather than three: enough to exercise the median estimator,
# short enough that the slow test stays a test.
REPS = 2


def _small_fixture():
    fixture = build_fixture(seed.ORDERS)
    fixture["requests"] = fixture["requests"][:16]
    fixture["warmup"] = 4
    return fixture


class CausalLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="causeway-e2e-")
        cls.template = os.path.join(cls.tmp, "template.db")
        cls.work = os.path.join(cls.tmp, "sandbox.db")
        seed.build(cls.template, AUDIT_ROWS)
        cls.fixture = _small_fixture()
        cls.sandbox = Sandbox(cls.template, cls.work).start()

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _p95(self, flags):
        return self.sandbox.measure(self.fixture, flags, REPS)["p95_ms"]

    def _experiment(self, hypothesis):
        specs = verdict.plan_phases(hypothesis, INCIDENT_STATE, self.fixture["id"])
        results = [verdict.PhaseResult(spec,
                                       self.sandbox.measure(self.fixture,
                                                            spec.flags, REPS))
                   for spec in specs]
        return verdict.annotate(results), verdict.decide(results)

    # -- the incident is real ---------------------------------------------
    def test_the_incident_state_is_separable_from_healthy(self):
        healthy = self._p95({"A": False, "B": False})
        self.assertTrue(verdict.separates(healthy, self._p95(INCIDENT_STATE)))

    def test_the_decoy_alone_is_latency_neutral(self):
        """If A moves the latency, A is not a decoy and the demo is dishonest."""
        healthy = self._p95({"A": False, "B": False})
        self.assertFalse(verdict.separates(healthy, self._p95({"A": True, "B": False})))

    def test_the_causal_change_alone_reproduces_the_failure(self):
        healthy = self._p95({"A": False, "B": False})
        self.assertTrue(verdict.separates(healthy, self._p95({"A": False, "B": True})))

    # -- the experiment decides -------------------------------------------
    def test_B_is_proven_by_controlled_ablation(self):
        _, decision = self._experiment("B")
        self.assertEqual(decision, verdict.PROVEN)

    def test_A_is_refuted(self):
        _, decision = self._experiment("A")
        self.assertEqual(decision, verdict.REFUTED)

    def test_the_observational_baseline_and_the_experiment_disagree(self):
        """The claim the whole demo makes, asserted end to end."""
        record = deploy_record()
        candidates, _ = localize(record)
        suspect = observational.top_suspect(
            observational.rank(candidates, record["incident"]))
        self.assertEqual(suspect, "A")
        _, decision = self._experiment(suspect)
        self.assertEqual(decision, verdict.REFUTED)

    # -- the protocol held ------------------------------------------------
    def test_a_control_was_measured_beside_every_phase(self):
        results, _ = self._experiment("B")
        self.assertEqual([r.spec.phase for r in results], list(verdict.PHASES))
        detail = verdict.explain(results)
        for evidence in verdict.EVIDENCE_PHASES:
            self.assertGreater(detail["controls"][evidence], 0.0)

    def test_every_phase_was_measured_more_than_once(self):
        results, _ = self._experiment("B")
        for result in results:
            self.assertEqual(result.observed["reps"], REPS)

    def test_the_ablation_holds_every_other_flag_fixed(self):
        results, _ = self._experiment("B")
        ablate = [r for r in results if r.spec.phase == verdict.PHASE_ABLATE][0]
        self.assertEqual(ablate.spec.flags["A"], INCIDENT_STATE["A"])
        self.assertFalse(ablate.spec.flags["B"])

    def test_the_environment_is_restored_between_phases(self):
        self.sandbox.restore()
        self.sandbox.set_flags(INCIDENT_STATE)
        self.assertEqual(self.sandbox.get_flags(), INCIDENT_STATE)
        self.sandbox.set_flags({"A": False, "B": False})
        self.assertEqual(self.sandbox.get_flags(), {"A": False, "B": False})

    def test_the_replay_is_read_only_so_restores_are_verified_not_recopied(self):
        before = self.sandbox.restores_verified
        self.sandbox.measure(self.fixture, {"A": False, "B": False}, 1)
        self.assertGreater(self.sandbox.restores_verified, before)


class EventStreamTests(unittest.TestCase):
    """The event contract the interface will be built against.

    Runs the real CLI in a subprocess against a freshly built database, so
    what this asserts is what a browser would receive.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="causeway-events-")
        seed.build(os.path.join(cls.tmp, "template.db"), AUDIT_ROWS)
        fixture = _small_fixture()
        fixtures = os.path.join(BACKEND, "fixtures")
        os.makedirs(fixtures, exist_ok=True)
        cls.fixture_path = os.path.join(fixtures, fixture["id"] + ".json")
        cls.wrote_fixture = not os.path.exists(cls.fixture_path)
        if cls.wrote_fixture:
            save_fixture(cls.fixture_path, fixture)
        with open(os.path.join(cls.tmp, "calibration.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({"audit_rows": AUDIT_ROWS, "orders": seed.ORDERS,
                       "healthy_p95_ms": 0.0, "incident_p95_ms": 0.0,
                       "ratio": 0.0, "note": "test"}, handle)

        cls.service_hash_before = _hash(service.__file__)

        env = dict(os.environ, CAUSEWAY_DATA=cls.tmp, CAUSEWAY_REPS=str(REPS))
        done = subprocess.run([sys.executable, "-m", "causeway.cli", "events"],
                              cwd=BACKEND, env=env, capture_output=True,
                              text=True, timeout=480)
        cls.done = done
        cls.events = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        cls.service_hash_after = _hash(service.__file__)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        if cls.wrote_fixture and os.path.exists(cls.fixture_path):
            os.remove(cls.fixture_path)

    def _of(self, kind):
        return [e for e in self.events if e["type"] == kind]

    def test_the_run_succeeds(self):
        self.assertEqual(self.done.returncode, 0, self.done.stderr)

    def test_it_ends_with_done(self):
        self.assertEqual(self.events[-1]["type"], "done")

    def test_the_pipeline_stages_appear_in_order(self):
        """B is PROVEN on this fixture, so the fix loop's own stages follow
        the causal experiment - Milestone 5 extends the stream, it does not
        replace what Milestone 2-4 already proved out."""
        stages = [e["stage"] for e in self._of("stage") if e["status"] == "done"]
        self.assertEqual(stages, ["incident_detected", "localization",
                                  "observational", "planning", "validation",
                                  "experiment", "fix_planning", "fix_validation",
                                  "fix_application", "fix_experiment"])

    def test_localisation_surfaces_two_candidates_and_explains_the_rest(self):
        event = self._of("candidates")[0]
        self.assertEqual([c["change_id"] for c in event["candidates"]], ["A", "B"])
        self.assertEqual(len(event["excluded"]), 2)
        for excluded in event["excluded"]:
            self.assertTrue(excluded["reason"])

    def test_the_observational_baseline_names_A(self):
        event = self._of("observational")[0]
        self.assertEqual(event["top_suspect"], "A")
        scores = {a["change_id"]: a["score"] for a in event["assessments"]}
        self.assertEqual(scores["A"], 0.961)
        self.assertEqual(scores["B"], 0.567)

    def test_every_plan_declares_its_provenance(self):
        plans = self._of("plan")
        self.assertEqual(len(plans), 2)
        for event in plans:
            provenance = event["provenance"]
            self.assertIn(provenance["kind"], ("gemini", "deterministic"))
            self.assertTrue(provenance["source"])
            if provenance["used_fallback"]:
                self.assertTrue(provenance["fallback_reason"])

    def test_every_plan_passes_all_eight_validator_checks(self):
        for event in self._of("validation"):
            self.assertEqual(event["total"], 8)
            self.assertEqual(event["passed"], 8)
            self.assertTrue(event["accepted"])

    def test_seven_phases_are_measured_per_hypothesis(self):
        for hypothesis in ("A", "B"):
            phases = [e["phase"] for e in self._of("phase_result")
                      if e["hypothesis"] == hypothesis]
            self.assertEqual(phases, list(verdict.PHASES))

    def test_each_evidence_phase_is_judged_against_a_local_control(self):
        judged = self._of("phase_judged")
        self.assertEqual(len(judged), 2 * len(verdict.EVIDENCE_PHASES))
        for event in judged:
            self.assertGreater(event["local_control_ms"], 0.0)
            self.assertIn(event["state"],
                          ("broken", "healthy", "inconclusive", "unstable"))

    def test_the_measurements_tell_the_high_low_high_story_for_B(self):
        judged = {e["phase"]: e for e in self._of("phase_judged")
                  if e["hypothesis"] == "B"}
        self.assertEqual(judged[verdict.PHASE_REPRODUCE]["state"], "broken")
        self.assertEqual(judged[verdict.PHASE_ABLATE]["state"], "healthy")
        self.assertEqual(judged[verdict.PHASE_RESTORE]["state"], "broken")

    def test_the_failure_survives_removing_A(self):
        judged = {e["phase"]: e for e in self._of("phase_judged")
                  if e["hypothesis"] == "A"}
        self.assertEqual(judged[verdict.PHASE_ABLATE]["state"], "broken")

    def test_the_verdicts_are_the_ones_the_measurements_imply(self):
        verdicts = {e["hypothesis"]: e["verdict"] for e in self._of("verdict")}
        self.assertEqual(verdicts, {"A": verdict.REFUTED, "B": verdict.PROVEN})

    def test_the_conclusion_reports_the_contrast(self):
        conclusion = self._of("conclusion")[0]
        self.assertEqual(conclusion["observational_top_suspect"], "A")
        self.assertEqual(conclusion["proven"], ["B"])
        self.assertEqual(conclusion["refuted"], ["A"])
        self.assertTrue(conclusion["correlation_selected_decoy"])

    def test_no_event_carries_a_verdict_before_the_experiment_ran(self):
        """The AI boundary, checked on the wire: nothing that reaches the
        interface before the sandbox runs may contain a decision."""
        first_phase = next(i for i, e in enumerate(self.events)
                           if e["type"] == "phase_result")
        early = json.dumps(self.events[:first_phase]).upper()
        for token in ("PROVEN", "REFUTED", "SUPPORTED", "UNRESOLVED"):
            self.assertNotIn('"%s"' % token, early)

    # -- Milestone 5: the fix loop, only for what was PROVEN --------------

    def test_root_cause_proven_fires_only_for_B(self):
        proven_events = self._of("root_cause_proven")
        self.assertEqual([e["hypothesis"] for e in proven_events], ["B"])
        self.assertEqual(proven_events[0]["verdict"], verdict.PROVEN)

    def test_a_fix_is_planned_and_validated_only_for_B(self):
        self.assertEqual([e["hypothesis"] for e in self._of("fix_plan")], ["B"])
        self.assertEqual([e["hypothesis"] for e in self._of("fix_validation")], ["B"])

    def test_A_never_receives_a_fix_event_of_any_kind(self):
        for kind in ("root_cause_proven", "fix_plan", "fix_validation",
                    "fix_apply", "fix_phase_start", "fix_phase_result",
                    "fix_phase_judged", "fix_verdict"):
            for event in self._of(kind):
                self.assertNotEqual(event.get("hypothesis"), "A",
                                    "A received a %r event" % kind)

    def test_every_fix_plan_declares_its_provenance(self):
        for event in self._of("fix_plan"):
            provenance = event["provenance"]
            self.assertIn(provenance["kind"], ("gemini", "deterministic"))
            self.assertTrue(provenance["source"])
            if provenance["used_fallback"]:
                self.assertTrue(provenance["fallback_reason"])

    def test_the_fix_validator_accepts_the_proposed_fix(self):
        for event in self._of("fix_validation"):
            self.assertTrue(event["accepted"])
            self.assertEqual(event["passed"], event["total"])

    def test_the_fix_operation_targets_the_registered_repair_surface(self):
        applied = self._of("fix_apply")
        self.assertEqual(len(applied), 1)
        operation = applied[0]["operation"]
        self.assertEqual(operation["target"], "SCANNING_PREDICATE")
        self.assertEqual(operation["after"], "order_id = ?")

    def test_five_fix_phases_are_measured(self):
        phases = [e["phase"] for e in self._of("fix_phase_result")]
        self.assertEqual(phases, list(fix_verdict.FIX_PHASES))

    def test_fix_experiment_start_lists_all_five_phases_up_front(self):
        starts = self._of("fix_experiment_start")
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["hypothesis"], "B")
        self.assertEqual(starts[0]["phases"], list(fix_verdict.FIX_PHASES))

    def test_the_fix_verdict_is_verified(self):
        """The known-safe repair restores index-friendly access, so the fix
        this fixture proposes for B should measurably recover."""
        fix_verdicts = self._of("fix_verdict")
        self.assertEqual(len(fix_verdicts), 1)
        self.assertEqual(fix_verdicts[0]["hypothesis"], "B")
        self.assertEqual(fix_verdicts[0]["verdict"], fix_verdict.VERIFIED)

    def test_no_fix_event_carries_a_measurement_before_its_own_phase_ran(self):
        """The same AI-boundary check as the causal experiment, applied to
        the fix loop: nothing before the first fix_phase_result may contain
        a fix verdict word."""
        first_fix_phase = next(i for i, e in enumerate(self.events)
                               if e["type"] == "fix_phase_result")
        early = json.dumps(self.events[:first_fix_phase]).upper()
        for token in (fix_verdict.VERIFIED, fix_verdict.FAILED, fix_verdict.UNRESOLVED):
            self.assertNotIn('"%s"' % token, early)

    def test_the_real_sandbox_service_source_is_unchanged_by_the_fix_loop(self):
        """The strongest form of the sandbox-isolation guarantee: hash the
        real, checked-in service.py before and after a full investigation
        that plans, validates, applies and measures a fix for B, run as a
        real subprocess exactly as the API would run it."""
        self.assertEqual(self.service_hash_before, self.service_hash_after,
                         "the real sandbox service source was modified by the run")


if __name__ == "__main__":
    unittest.main()
