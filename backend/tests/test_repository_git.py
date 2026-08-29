"""Safe repository acquisition: clone into a disposable workspace, argument-
array subprocess invocation, timeout handling, cleanup, and that a failed or
slow clone never leaves anything behind. Every clone here targets a local
git repository - no live GitHub, no network."""
from __future__ import annotations

import inspect
import os
import subprocess
import unittest

from causeway.repository import git as repogit
from causeway.repository.errors import RepositoryRejected
from causeway.repository.urlcheck import RepoRef
from tests.repo_fixtures import local_repo

REF = RepoRef(owner="foo", name="bar", url="https://github.com/foo/bar")


class SafeInvocationTests(unittest.TestCase):
    def test_no_shell_true_appears_anywhere_in_this_module(self):
        """The whole point of an argument-array subprocess call is defeated
        the moment shell=True appears once."""
        source = inspect.getsource(repogit)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("shell =True", source)
        self.assertNotIn("shell= True", source)

    def test_git_is_always_invoked_as_an_argument_list(self):
        source = inspect.getsource(repogit._git)
        self.assertIn('["git"] + args', source)


class CloneTests(unittest.TestCase):
    def test_a_clone_lands_in_a_fresh_temporary_directory(self):
        with local_repo(copy_demo=True) as source:
            cloned = repogit.clone(REF, source=source)
            try:
                self.assertTrue(os.path.isdir(cloned.path))
                self.assertNotEqual(os.path.realpath(cloned.path),
                                    os.path.realpath(source))
                self.assertTrue(os.path.isfile(os.path.join(cloned.path, "causeway.json")))
            finally:
                cloned.cleanup()

    def test_the_clone_captures_the_real_commit_sha(self):
        with local_repo(copy_demo=True) as source:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source,
                                    check=True, capture_output=True, text=True)
            expected_sha = result.stdout.strip()
            cloned = repogit.clone(REF, source=source)
            try:
                self.assertEqual(cloned.commit_sha, expected_sha)
            finally:
                cloned.cleanup()

    def test_cleanup_removes_the_whole_workspace(self):
        with local_repo(copy_demo=True) as source:
            cloned = repogit.clone(REF, source=source)
            workdir = cloned.workdir
            cloned.cleanup()
            self.assertFalse(os.path.exists(workdir))

    def test_the_original_local_checkout_is_never_modified(self):
        """The disposable clone must never write back to what it was cloned
        from - the same guarantee that matters for a real GitHub clone not
        touching the developer's own checkout of Causeway."""
        with local_repo(copy_demo=True) as source:
            before = sorted(os.listdir(source))
            cloned = repogit.clone(REF, source=source)
            try:
                after = sorted(os.listdir(source))
                self.assertEqual(before, after)
            finally:
                cloned.cleanup()

    def test_cloning_a_nonexistent_source_is_rejected_not_a_crash(self):
        with self.assertRaises(RepositoryRejected) as caught:
            repogit.clone(REF, source=os.path.join(
                os.path.dirname(__file__), "no-such-repository-here"))
        self.assertEqual(caught.exception.stage, "clone")

    def test_a_failed_clone_leaves_nothing_behind(self):
        before = _tmp_entries()
        try:
            repogit.clone(REF, source="/definitely/not/a/repository")
        except RepositoryRejected:
            pass
        after = _tmp_entries()
        self.assertEqual(before, after)

    def test_a_clone_that_exceeds_its_timeout_is_rejected_as_a_timeout(self):
        """A git subprocess that would hang (e.g. on an interactive prompt)
        must not hang the investigation - it has to time out and be
        reported as such, not raise an unhandled TimeoutExpired."""
        with local_repo(copy_demo=True) as source:
            with self.assertRaises(RepositoryRejected) as caught:
                repogit.clone(REF, source=source, timeout=0.0001)
            self.assertEqual(caught.exception.stage, "clone")
            self.assertIn("longer than", caught.exception.reason)

    def test_repository_provided_hooks_do_not_run(self):
        """A post-checkout hook in the source repository must not execute
        during the clone - core.hooksPath is pointed at an empty directory."""
        marker = os.path.join(os.path.dirname(__file__), "_hook_fired_should_not_exist")
        if os.path.exists(marker):
            os.remove(marker)
        hook_body = (
            "#!/bin/sh\n"
            "printf x > %s\n" % marker.replace("\\", "/")
        )
        with local_repo({"README.md": "x"}) as source:
            hooks_dir = os.path.join(source, ".git", "hooks")
            os.makedirs(hooks_dir, exist_ok=True)
            hook_path = os.path.join(hooks_dir, "post-checkout")
            with open(hook_path, "w", newline="\n") as handle:
                handle.write(hook_body)
            os.chmod(hook_path, 0o755)

            cloned = repogit.clone(REF, source=source)
            try:
                self.assertFalse(os.path.exists(marker),
                                 "a repository-provided hook fired during clone")
            finally:
                cloned.cleanup()
                if os.path.exists(marker):
                    os.remove(marker)


def _tmp_entries():
    import tempfile
    base = tempfile.gettempdir()
    return {name for name in os.listdir(base) if name.startswith("causeway-repo-")}


if __name__ == "__main__":
    unittest.main()
