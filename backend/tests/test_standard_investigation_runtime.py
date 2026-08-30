"""Real runtime observation, wired into the standard (manifest-less)
investigation path: causeway/standard_investigation.py's `runtime_observed`
events and the `runtime_exception_resolved` field on
`requested_change_verdict`.

No live GitHub, no live Gemini: causeway.repository.clone is redirected at
a local git repository (tests.repo_fixtures.local_repo), and where a
successful patch is needed, causeway.patch.gemini.GeminiPatchPlanner's own
transport is mocked - the same seam tests/test_standard_repository.py and
tests/test_patch_timeout.py already use. Every assertion here runs the real
causeway.orchestrator.investigate(...) generator end to end.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from causeway import orchestrator
from causeway.patch.gemini import GeminiPatchPlanner
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

REPO_URL = "https://github.com/o/n"

CRASHING_SOURCE = (
    "def inner():\n"
    "    numbers = [1, 2, 3]\n"
    "    return numbers[5]\n"
    "\n"
    "inner()\n"
)
FIXED_SOURCE = (
    "def inner():\n"
    "    numbers = [1, 2, 3]\n"
    "    return numbers[0]\n"
    "\n"
    "inner()\n"
)


def _cloning_from(local_source: str):
    def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
        return repogit.clone(ref, timeout=timeout, source=local_source)
    return _clone


def _envelope(patch: dict) -> dict:
    return {"candidates": [{"content": {"role": "model",
                                        "parts": [{"text": json.dumps(patch)}]}}]}


class SuccessfulFixEndToEndTests(unittest.TestCase):
    """A single crashing file, a mocked Gemini fix that actually resolves
    the crash: proves the whole pipeline in order - observed before any
    patch is proposed, observed again after, compared, and reported -
    without ever calling VERIFIED, which this path has not earned."""

    @classmethod
    def setUpClass(cls):
        with local_repo({"app.py": CRASHING_SOURCE}) as source:
            patch = {
                "summary": "Use a valid list index",
                "files": [{"path": "app.py",
                          "hunks": [{"before": CRASHING_SOURCE, "after": FIXED_SOURCE}]}],
                "reasoning_summary": "numbers[5] is out of range for a 3-element list; "
                                     "numbers[0] is always valid.",
            }
            transport = mock.Mock(return_value=_envelope(patch))
            with mock.patch("causeway.patch.gemini.api_key_from_env",
                            return_value="fake-test-key"), \
                 mock.patch.object(GeminiPatchPlanner, "_post", transport), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=False,
                    instruction="fix the IndexError in app.py", mode="diagnose_and_fix"))
            cls.transport = transport

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def _types(self):
        return [e["type"] for e in self.events]

    def test_a_before_observation_is_emitted_and_shows_the_real_crash(self):
        before = [e for e in self._of("runtime_observed") if e["phase"] == "before"]
        self.assertEqual(len(before), 1)
        self.assertTrue(before[0]["crashed"])
        self.assertEqual(before[0]["traceback"]["exception_type"], "IndexError")
        self.assertEqual(before[0]["traceback"]["file"], "app.py")

    def test_the_before_observation_happens_before_any_patch_is_proposed(self):
        types = self._types()
        self.assertLess(types.index("runtime_observed"), types.index("patch_plan"))

    def test_an_after_observation_is_emitted_and_shows_a_clean_run(self):
        after = [e for e in self._of("runtime_observed") if e["phase"] == "after"]
        self.assertEqual(len(after), 1)
        self.assertTrue(after[0]["exited_cleanly"])
        self.assertFalse(after[0]["crashed"])

    def test_the_verdict_reports_the_exception_resolved(self):
        verdict = self._of("requested_change_verdict")[0]
        self.assertTrue(verdict["runtime_exception_resolved"])
        self.assertIn("IndexError", verdict["reason"])

    def test_the_verdict_word_is_never_upgraded_to_verified(self):
        """The single most important regression to lock down: real,
        resolved runtime evidence is additional reported fact, never a
        route to the word this path has not earned."""
        verdict = self._of("requested_change_verdict")[0]
        self.assertEqual(verdict["verdict"], "IMPLEMENTED_VERIFICATION_INCOMPLETE")
        self.assertNotEqual(verdict["verdict"], "VERIFIED")

    def test_the_runtime_evidence_reached_the_prompt_gemini_was_sent(self):
        body = self.transport.call_args.args[2]
        prompt_text = body["contents"][0]["parts"][0]["text"]
        self.assertIn("OBSERVED RUNTIME BEHAVIOUR", prompt_text)
        self.assertIn("IndexError", prompt_text)


class UnresolvedCrashEndToEndTests(unittest.TestCase):
    """A mocked "fix" that does not actually touch the crashing line: the
    same exception must still be observed after, and the verdict must say
    the crash is unresolved - never silently upgraded because a patch was
    at least applied."""

    @classmethod
    def setUpClass(cls):
        unrelated_change = CRASHING_SOURCE + "\n# a harmless comment\n"
        with local_repo({"app.py": CRASHING_SOURCE}) as source:
            patch = {
                "summary": "Add a clarifying comment",
                "files": [{"path": "app.py",
                          "hunks": [{"before": CRASHING_SOURCE, "after": unrelated_change}]}],
                "reasoning_summary": "documented the function for future readers.",
            }
            transport = mock.Mock(return_value=_envelope(patch))
            with mock.patch("causeway.patch.gemini.api_key_from_env",
                            return_value="fake-test-key"), \
                 mock.patch.object(GeminiPatchPlanner, "_post", transport), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=False,
                    instruction="fix the IndexError in app.py", mode="diagnose_and_fix"))

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_the_verdict_reports_the_exception_as_not_resolved(self):
        verdict = self._of("requested_change_verdict")[0]
        self.assertFalse(verdict["runtime_exception_resolved"])
        self.assertIn("still raises", verdict["reason"])


class LongRunningEntrypointTests(unittest.TestCase):
    """A blocking, service-shaped entrypoint must be reported inconclusive,
    never as a false FAILED - and never blocks the rest of the run for more
    than the runtime timeout."""

    @classmethod
    def setUpClass(cls):
        with local_repo({"app.py": "import time\ntime.sleep(60)\n"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)), \
                 mock.patch.dict("os.environ",
                                 {"CAUSEWAY_RUNTIME_TIMEOUT_SECONDS": "2"}):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction="fix the code", mode="diagnose_only"))

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_the_observation_is_reported_as_timed_out_not_crashed(self):
        before = [e for e in self._of("runtime_observed") if e["phase"] == "before"]
        self.assertEqual(len(before), 1)
        self.assertTrue(before[0]["timed_out"])
        self.assertFalse(before[0]["crashed"])
        self.assertIn("long-running service", before[0]["note"])


class AmbiguousRepositoryTests(unittest.TestCase):
    """Two Python files, neither a recognised entrypoint name: never guess
    which one to run."""

    @classmethod
    def setUpClass(cls):
        with local_repo({"a.py": "print('a')\n", "b.py": "print('b')\n"}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction="fix the code", mode="diagnose_only"))

    def test_no_runtime_observed_event_is_emitted_at_all(self):
        types = [e["type"] for e in self.events]
        self.assertNotIn("runtime_observed", types)


class DiagnoseOnlyTests(unittest.TestCase):
    """diagnose_only still gets real, read-only evidence - that mode's
    whole point - but never a patch, and never an "after" observation
    (there is nothing to compare against)."""

    @classmethod
    def setUpClass(cls):
        with local_repo({"app.py": CRASHING_SOURCE}) as source:
            with mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url=REPO_URL, offline=True,
                    instruction="find why this crashes", mode="diagnose_only"))

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_a_before_observation_is_still_emitted(self):
        before = [e for e in self._of("runtime_observed") if e["phase"] == "before"]
        self.assertEqual(len(before), 1)
        self.assertTrue(before[0]["crashed"])

    def test_no_after_observation_no_patch_and_no_verdict_are_emitted(self):
        after = [e for e in self._of("runtime_observed") if e["phase"] == "after"]
        self.assertEqual(after, [])
        self.assertEqual(self._of("patch_plan"), [])
        self.assertEqual(self._of("requested_change_verdict"), [])


if __name__ == "__main__":
    unittest.main()
