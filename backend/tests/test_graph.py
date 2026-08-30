"""build_graph is a pure function of an investigation's own event buffer -
every test here constructs that buffer directly, the same way
frontend/src/graph.test.ts exercises buildCausalGraph without running the
app, and the same way the rest of this suite exercises causeway.verdict
without running the sandbox.
"""
import unittest

from causeway.graph import build_graph
from causeway.incidents import Incident


def incident_event():
    return {"type": "incident", "incident": {
        "id": "inc-1", "service": "order-service", "title": "Order latency regression",
        "symptom": "p95 latency up 15x", "detected_at": "2026-08-30T00:00:00Z",
    }, "repetitions": 3}


def candidates_event(*ids):
    return {"type": "candidates", "deploys_considered": len(ids), "excluded": [],
            "candidates": [
                {"change_id": cid, "sha": "%ssha00000000" % cid, "branch": "feature/%s" % cid,
                 "service": "order-service", "summary": "Change %s" % cid,
                 "deployed_at": "2026-08-29T23:00:00Z", "seconds_before_detection": 120,
                 "files_changed": 3, "lines_changed": 40, "changed_files": ["src/%s.py" % cid]}
                for cid in ids
            ]}


def hypotheses_event(*ids):
    return {"type": "hypotheses", "sources": ["order_service/db.py"], "detectors": ["predicate-wrap"],
            "testable": list(ids),
            "hypotheses": [
                {"id": hid, "label": "Hypothesis %s" % hid, "file": "order_service/db.py", "line": 144,
                 "symbol": "get_order_audit", "kind": "predicate",
                 "observed": "WHERE normalize(order_id) = ?", "counterfactual": "WHERE order_id = ?",
                 "evidence": "predicate wraps an indexed column", "reason": "this can defeat the index",
                 "detector": "predicate-wrap", "testable": True, "context": []}
                for hid in ids
            ]}


def plan_event(hid):
    return {"type": "plan", "hypothesis": hid, "plan": {}, "validation": {},
            "provenance": {"source": "deterministic", "kind": "deterministic",
                           "proposed_by": "deterministic", "used_fallback": False,
                           "fallback_reason": ""}}


def experiment_start_event(hid, phases=("control-1", "reproduce")):
    return {"type": "experiment_start", "hypothesis": hid, "phases": list(phases),
            "holding_fixed": []}


def verdict_event(hid, verdict, reason="measured"):
    return {"type": "verdict", "hypothesis": hid, "verdict": verdict, "reason": reason,
            "detail": {}, "phases": []}


def repository_loaded_event():
    return {"type": "repository_loaded", "owner": "acme", "name": "order-service",
            "url": "https://github.com/acme/order-service", "commit_sha": "abc123",
            "service": "order-service", "runtime": "python", "verification": "latency_p95",
            "entrypoint": "app.py", "sources": ["order_service/db.py"],
            "patchable": ["order_service/db.py"], "database": None, "workload": None}


def root_cause_proven_event(hid, verdict="PROVEN", label=None):
    return {"type": "root_cause_proven", "hypothesis": hid, "verdict": verdict, "label": label}


def fix_verdict_event(hid, verdict="VERIFIED", reason="patched build recovered"):
    return {"type": "fix_verdict", "hypothesis": hid, "verdict": verdict, "reason": reason, "phases": []}


class BuildGraphTests(unittest.TestCase):

    def test_empty_events_produce_empty_graph(self):
        graph = build_graph([])
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])

    def test_incident_only(self):
        graph = build_graph([incident_event()])
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["nodes"][0]["id"], "incident")
        self.assertEqual(graph["nodes"][0]["type"], "incident")
        self.assertEqual(graph["edges"], [])

    def test_candidate_not_yet_tested_is_a_suspect_not_a_cause(self):
        graph = build_graph([incident_event(), candidates_event("A")])
        edge = next(e for e in graph["edges"] if e["source"] == "candidate:A")
        self.assertEqual(edge["target"], "incident")
        self.assertEqual(edge["strength"], "candidate")
        self.assertNotIn("verified", edge["label"].lower())

    def test_experiment_splices_in_and_labels_by_verdict(self):
        events = [incident_event(), candidates_event("B"), plan_event("B"),
                 experiment_start_event("B"), verdict_event("B", "PROVEN")]
        graph = build_graph(events)
        experiment = next(n for n in graph["nodes"] if n["id"] == "experiment:B")
        self.assertEqual(experiment["type"], "experiment")
        self.assertEqual(experiment["status"], "PROVEN")

        tested_by = next(e for e in graph["edges"]
                         if e["source"] == "candidate:B" and e["target"] == "experiment:B")
        self.assertEqual(tested_by["strength"], "link")

        verdict_edge = next(e for e in graph["edges"]
                            if e["source"] == "experiment:B" and e["target"] == "incident")
        self.assertEqual(verdict_edge["strength"], "proven")
        self.assertEqual(verdict_edge["label"], "verified causal relationship")

        direct = [e for e in graph["edges"] if e["source"] == "candidate:B" and e["target"] == "incident"]
        self.assertEqual(direct, [])

    def test_refuted_is_labelled_refuted_not_a_cause(self):
        events = [incident_event(), candidates_event("A"), plan_event("A"),
                 experiment_start_event("A"), verdict_event("A", "REFUTED")]
        graph = build_graph(events)
        verdict_edge = next(e for e in graph["edges"] if e["target"] == "incident")
        self.assertEqual(verdict_edge["strength"], "refuted")
        self.assertEqual(verdict_edge["label"], "refuted")

    def test_repository_code_hypothesis_wired_through_repository_node(self):
        events = [incident_event(), repository_loaded_event(), hypotheses_event("h1")]
        graph = build_graph(events)
        self.assertTrue(any(n["id"] == "repository" for n in graph["nodes"]))
        code_node = next(n for n in graph["nodes"] if n["id"] == "code:h1")
        self.assertEqual(code_node["type"], "code_change")
        self.assertEqual(code_node["description"], "order_service/db.py:144")
        contains = next(e for e in graph["edges"]
                        if e["source"] == "repository" and e["target"] == "code:h1")
        self.assertEqual(contains["strength"], "link")

    def test_fix_node_attaches_to_its_experiment(self):
        events = [incident_event(), candidates_event("B"), plan_event("B"),
                 experiment_start_event("B"), verdict_event("B", "PROVEN"),
                 root_cause_proven_event("B", label="Change B"),
                 fix_verdict_event("B", "VERIFIED")]
        graph = build_graph(events)
        fix_node = next(n for n in graph["nodes"] if n["id"] == "fix:B")
        self.assertEqual(fix_node["type"], "fix")
        self.assertEqual(fix_node["status"], "VERIFIED")
        self.assertTrue(any(e["source"] == "experiment:B" and e["target"] == "fix:B"
                            for e in graph["edges"]))

    def test_no_fix_node_is_fabricated_when_nothing_was_proposed(self):
        graph = build_graph([incident_event()])
        self.assertFalse(any(n["type"] == "fix" for n in graph["nodes"]))

    def test_prediction_node_only_when_backend_linked_it_to_this_run(self):
        linked = Incident(
            incident_id="pred-1", service="order-service", detector="latency_degradation",
            predicted_failure="latency degradation", risk_score=82.0,
            evidence=("p95 up 60% over 30 min",), current_values={}, trends={},
            eta_seconds=900.0, sample_count=12, created_at=0.0,
            status="INVESTIGATION_STARTED", run_id="run-1",
        )
        graph = build_graph([incident_event()], incidents=[linked], run_id="run-1")
        node = next((n for n in graph["nodes"] if n["type"] == "prediction"), None)
        self.assertIsNotNone(node)
        self.assertTrue(any(e["source"] == "prediction" and e["target"] == "incident"
                            for e in graph["edges"]))

    def test_no_prediction_node_for_an_unrelated_run(self):
        other = Incident(
            incident_id="pred-2", service="order-service", detector="latency_degradation",
            predicted_failure="latency degradation", risk_score=82.0,
            evidence=(), current_values={}, trends={}, eta_seconds=None, sample_count=12,
            created_at=0.0, status="INVESTIGATION_STARTED", run_id="some-other-run",
        )
        graph = build_graph([incident_event()], incidents=[other], run_id="run-1")
        self.assertFalse(any(n["type"] == "prediction" for n in graph["nodes"]))

    def test_deterministic_same_events_same_graph(self):
        events = [incident_event(), candidates_event("A", "B"), plan_event("A"), plan_event("B"),
                 experiment_start_event("A"), verdict_event("A", "REFUTED"),
                 experiment_start_event("B"), verdict_event("B", "PROVEN")]
        self.assertEqual(build_graph(events), build_graph(events))


if __name__ == "__main__":
    unittest.main()
