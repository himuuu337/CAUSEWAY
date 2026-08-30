"""causeway.languages._run_once and causeway.languages.python_runtime: real
subprocess execution, on this machine, of real temporary scripts - no
mocking of the subprocess layer itself, the same philosophy
tests/test_languages.py already applies to py_compile/node/javac. Windows
timeout/kill and path-casing behaviour is exactly where this needs to be
proven for real rather than assumed.

Every fixture is a disposable local repository (tests.repo_fixtures.
local_repo) - no network anywhere in this file.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from causeway.languages import _run_once, python_runtime
from tests.repo_fixtures import local_repo

# ------------------------------------------------------- _run_once itself --


class TimeoutFromEnvTests(unittest.TestCase):
    def _with_env(self, value):
        return mock.patch.dict(os.environ, {_run_once.TIMEOUT_ENV_VAR: value})

    def test_the_default_is_8_seconds(self):
        self.assertEqual(_run_once.DEFAULT_TIMEOUT_SECONDS, 8.0)

    def test_an_unset_variable_uses_the_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_run_once.TIMEOUT_ENV_VAR, None)
            self.assertEqual(_run_once.timeout_from_env(), 8.0)

    def test_a_valid_value_is_honoured(self):
        with self._with_env("5"):
            self.assertEqual(_run_once.timeout_from_env(), 5.0)

    def test_non_numeric_text_falls_back_to_the_default(self):
        with self._with_env("not-a-number"):
            self.assertEqual(_run_once.timeout_from_env(), _run_once.DEFAULT_TIMEOUT_SECONDS)

    def test_nan_falls_back_to_the_default(self):
        with self._with_env("nan"):
            self.assertEqual(_run_once.timeout_from_env(), _run_once.DEFAULT_TIMEOUT_SECONDS)

    def test_a_value_below_the_minimum_is_clamped_up(self):
        with self._with_env("0.1"):
            self.assertEqual(_run_once.timeout_from_env(), _run_once.MIN_TIMEOUT)

    def test_a_value_above_the_maximum_is_clamped_down(self):
        with self._with_env("9999"):
            self.assertEqual(_run_once.timeout_from_env(), _run_once.MAX_TIMEOUT)


class RestrictedEnvTests(unittest.TestCase):
    def test_a_secret_shaped_variable_is_not_carried_forward(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "super-secret"}):
            self.assertNotIn("GEMINI_API_KEY", _run_once.restricted_env())

    def test_an_unrelated_ordinary_variable_is_also_not_carried_forward(self):
        """Proves an allowlist, not a blocklist of secret-shaped names: a
        variable with no plausible secret-sounding name is stripped too."""
        with mock.patch.dict(os.environ, {"SOME_RANDOM_TEST_VAR": "hello"}):
            self.assertNotIn("SOME_RANDOM_TEST_VAR", _run_once.restricted_env())

    def test_path_is_carried_forward_so_python_can_actually_start(self):
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}):
            self.assertIn("PATH", _run_once.restricted_env())

    def test_this_is_provably_enforced_in_a_real_child_process(self):
        """The end-to-end proof: a real subprocess that tries to read a
        secret-shaped variable from its own environment gets None, not the
        real value - not just a claim about what restricted_env() returns."""
        with local_repo({"app.py": (
            "import os\n"
            "print(os.environ.get('GEMINI_API_KEY'), os.environ.get('SOME_RANDOM_TEST_VAR'))\n"
        )}) as root:
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "super-secret",
                                              "SOME_RANDOM_TEST_VAR": "hello"}):
                observation = python_runtime.observe(root, "app.py")
        self.assertTrue(observation.exited_cleanly)
        self.assertIn("None None", observation.stdout_tail)


class RunPythonScriptTests(unittest.TestCase):
    def test_a_crashing_script_is_captured_with_a_nonzero_exit(self):
        with local_repo({"app.py": "raise RuntimeError('boom')\n"}) as root:
            import sys
            run = _run_once.run_python_script([sys.executable, "app.py"], cwd=root)
        self.assertFalse(run.ok)
        self.assertFalse(run.timed_out)
        self.assertEqual(run.returncode, 1)
        self.assertIn("RuntimeError", run.stderr)

    def test_a_clean_script_exits_ok(self):
        with local_repo({"app.py": "print('hello')\n"}) as root:
            import sys
            run = _run_once.run_python_script([sys.executable, "app.py"], cwd=root)
        self.assertTrue(run.ok)
        self.assertEqual(run.returncode, 0)
        self.assertIn("hello", run.stdout)

    def test_a_hanging_script_times_out_with_no_output(self):
        with local_repo({"app.py": "import time\ntime.sleep(60)\n"}) as root:
            import sys
            run = _run_once.run_python_script([sys.executable, "app.py"], cwd=root, timeout=2.0)
        self.assertTrue(run.timed_out)
        self.assertIsNone(run.returncode)
        self.assertEqual(run.stdout, "")
        self.assertEqual(run.stderr, "")

    def test_output_flushed_before_a_hang_is_not_discarded_on_timeout(self):
        with local_repo({"app.py": (
            "import sys, time\n"
            "print('partial output', file=sys.stderr, flush=True)\n"
            "time.sleep(60)\n"
        )}) as root:
            import sys
            run = _run_once.run_python_script([sys.executable, "app.py"], cwd=root, timeout=2.0)
        self.assertTrue(run.timed_out)
        self.assertIn("partial output", run.stderr)


# ------------------------------------------------------ select_entrypoint --


class SelectEntrypointTests(unittest.TestCase):
    def test_a_guessed_entrypoint_wins(self):
        self.assertEqual(
            python_runtime.select_entrypoint("app.py", ("app.py", "helpers.py"), "python"),
            "app.py")

    def test_a_single_python_file_with_no_guessed_entrypoint_is_used(self):
        self.assertEqual(python_runtime.select_entrypoint("", ("script.py",), "python"),
                         "script.py")

    def test_two_ambiguous_files_with_no_guessed_entrypoint_declines(self):
        self.assertEqual(
            python_runtime.select_entrypoint("", ("a.py", "b.py"), "python"), "")

    def test_a_non_python_repository_never_attempts_execution(self):
        self.assertEqual(python_runtime.select_entrypoint("main.js", ("main.js",), "javascript"),
                         "")

    def test_no_python_sources_at_all_declines(self):
        self.assertEqual(python_runtime.select_entrypoint("", (), "python"), "")


# -------------------------------------------------------------- observe() --


class ObserveCrashTests(unittest.TestCase):
    def test_a_runtime_crash_is_captured_with_a_real_file_and_line(self):
        with local_repo({"app.py": (
            "def inner():\n"
            "    numbers = [1, 2, 3]\n"
            "    return numbers[5]\n"
            "inner()\n"
        )}) as root:
            observation = python_runtime.observe(root, "app.py")
        self.assertTrue(observation.attempted)
        self.assertTrue(observation.crashed)
        self.assertFalse(observation.exited_cleanly)
        self.assertIsNotNone(observation.traceback)
        self.assertEqual(observation.traceback.exception_type, "IndexError")
        self.assertEqual(observation.traceback.file, "app.py")
        self.assertEqual(observation.traceback.line, 3)

    def test_a_syntax_error_is_captured(self):
        with local_repo({"app.py": "def hello()\n    pass\n"}) as root:
            observation = python_runtime.observe(root, "app.py")
        self.assertTrue(observation.crashed)
        self.assertEqual(observation.traceback.exception_type, "SyntaxError")


class ObserveCleanRunTests(unittest.TestCase):
    def test_a_clean_run_is_reported_as_such(self):
        with local_repo({"app.py": "print('all good')\n"}) as root:
            observation = python_runtime.observe(root, "app.py")
        self.assertTrue(observation.exited_cleanly)
        self.assertFalse(observation.crashed)
        self.assertIsNone(observation.traceback)


class ObserveTimeoutTests(unittest.TestCase):
    def test_a_long_running_service_style_script_is_reported_inconclusive_not_failed(self):
        with local_repo({"app.py": "import time\ntime.sleep(60)\n"}) as root:
            observation = python_runtime.observe(root, "app.py", timeout=2.0)
        self.assertTrue(observation.timed_out)
        self.assertFalse(observation.crashed)
        self.assertFalse(observation.exited_cleanly)
        self.assertIn("long-running service", observation.note)

    def test_a_crash_during_startup_before_a_hang_is_still_reported_as_a_crash(self):
        with local_repo({"app.py": (
            "import sys, time\n"
            "raise RuntimeError('failed during startup')\n"
            "time.sleep(9999)\n"
        )}) as root:
            observation = python_runtime.observe(root, "app.py", timeout=3.0)
        self.assertTrue(observation.crashed)
        self.assertEqual(observation.traceback.exception_type, "RuntimeError")


class ObserveNotAttemptedTests(unittest.TestCase):
    def test_no_entrypoint_means_nothing_is_run(self):
        with local_repo({"app.py": "print('hi')\n"}) as root:
            observation = python_runtime.observe(root, "")
        self.assertFalse(observation.attempted)


# ------------------------------------------------------------- compare() --


def _crashed(exc_type="IndexError", file="app.py", line=3):
    from causeway.languages.python_traceback import TracebackFinding
    return python_runtime.RuntimeObservation(
        attempted=True, entrypoint="app.py", exited_cleanly=False, timed_out=False,
        crashed=True, traceback=TracebackFinding(
            kind="runtime", exception_type=exc_type, message="x", file=file, line=line,
            function="f", frame_available=True, raw=""),
        stdout_tail="", stderr_tail="", duration_s=0.1, note="crashed")


def _clean():
    return python_runtime.RuntimeObservation(
        attempted=True, entrypoint="app.py", exited_cleanly=True, timed_out=False,
        crashed=False, traceback=None, stdout_tail="", stderr_tail="", duration_s=0.1,
        note="ran cleanly")


def _not_attempted():
    return python_runtime.RuntimeObservation(
        attempted=False, entrypoint="", exited_cleanly=False, timed_out=False, crashed=False,
        traceback=None, stdout_tail="", stderr_tail="", duration_s=0.0, note="not attempted")


class CompareTests(unittest.TestCase):
    def test_before_never_attempted_is_nothing_to_compare(self):
        self.assertIsNone(python_runtime.compare(_not_attempted(), _clean()))

    def test_before_did_not_crash_is_nothing_to_compare(self):
        self.assertIsNone(python_runtime.compare(_clean(), _clean()))

    def test_crash_resolved_by_a_clean_run_is_true(self):
        self.assertTrue(python_runtime.compare(_crashed(), _clean()))

    def test_the_identical_exception_recurring_is_false(self):
        self.assertFalse(python_runtime.compare(_crashed(), _crashed()))

    def test_a_genuinely_different_exception_after_is_true(self):
        self.assertTrue(python_runtime.compare(_crashed(exc_type="IndexError"),
                                               _crashed(exc_type="KeyError")))

    def test_the_same_exception_at_a_different_line_is_true(self):
        """A different line means the fix moved the problem, or this is a
        genuinely different bug - not proof of an unresolved one."""
        self.assertTrue(python_runtime.compare(_crashed(line=3), _crashed(line=10)))


if __name__ == "__main__":
    unittest.main()
