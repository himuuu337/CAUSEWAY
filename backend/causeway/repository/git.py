"""Safe acquisition of a repository into a disposable, isolated workspace.

Every subprocess call here is an argument array, never a shell string - and
the clone URL is rebuilt from a validated RepoRef's owner/name, never the
caller's raw text, so nothing user-supplied reaches a command line unchecked
even though urlcheck.validate_url has already vetted it once.

Repository-provided hooks never run: `core.hooksPath` is pointed at a fresh,
empty directory for the clone, so any hook git would otherwise invoke finds
nothing there. Credential prompts are disabled outright, so a private
repository (no credentials configured, which this milestone does not support)
fails cleanly instead of hanging on a login prompt.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass

from causeway.repository.errors import RepositoryRejected
from causeway.repository.urlcheck import RepoRef

CLONE_TIMEOUT = 60.0
REV_PARSE_TIMEOUT = 10.0

_GIT_ENV_OVERRIDES = {
    "GIT_TERMINAL_PROMPT": "0",   # never prompt for a login on a private repo
    "GIT_ASKPASS": "echo",        # belt-and-braces: any askpass call gets an empty answer
}


def _force_remove(func, path, exc_info):
    """git leaves files under .git/objects read-only. A plain rmtree stops
    on the first one of those on Windows even with ignore_errors, silently
    leaving the workspace behind - clear the attribute and retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _rmtree(path: str) -> None:
    shutil.rmtree(path, onerror=_force_remove)


@dataclass
class ClonedRepo:
    path: str
    commit_sha: str
    workdir: str   # the disposable parent directory; removed whole on cleanup

    def cleanup(self) -> None:
        _rmtree(self.workdir)


def _git(args, cwd=None, timeout=CLONE_TIMEOUT):
    env = dict(os.environ, **_GIT_ENV_OVERRIDES)
    try:
        return subprocess.run(
            ["git"] + args, cwd=cwd, env=env, timeout=timeout,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=False)
    except FileNotFoundError:
        raise RepositoryRejected("clone", "git is not available on this machine")
    except subprocess.TimeoutExpired:
        raise RepositoryRejected(
            "clone", "cloning the repository took longer than %.0fs" % timeout)


def clone(ref: RepoRef, timeout: float = CLONE_TIMEOUT, source: str = None) -> ClonedRepo:
    """Shallow-clone a validated repository into a fresh temporary directory.

    Only ever called with a RepoRef that has already passed
    urlcheck.validate_url - the URL cloned is rebuilt from its owner/name
    here, never taken as raw text.

    `source` overrides the derived https://github.com/... URL. It exists
    only so tests can point this at a local repository without depending on
    live GitHub or the network; the orchestrator never passes it; production
    calls always clone the real GitHub URL a validated RepoRef names.
    """
    workdir = tempfile.mkdtemp(prefix="causeway-repo-")
    try:
        dest = os.path.join(workdir, "repo")
        hooks_dir = os.path.join(workdir, "_no_hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        clone_url = source if source is not None else (
            "https://github.com/%s/%s.git" % (ref.owner, ref.name))

        # _git() itself raises RepositoryRejected on a timeout or a missing
        # git binary - that exception propagates straight through this
        # try/except, which exists only to remove `workdir` first.
        result = _git([
            "clone", "--depth", "1", "--single-branch", "--no-tags",
            "--config", "core.hooksPath=%s" % hooks_dir,
            "--config", "credential.helper=",
            clone_url, dest,
        ], timeout=timeout)

        if result.returncode != 0:
            tail = [line for line in (result.stdout or "").strip().splitlines() if line.strip()]
            detail = tail[-1] if tail else "git clone failed"
            raise RepositoryRejected("clone", "could not clone %s: %s" % (ref.url, detail))

        sha_result = _git(["rev-parse", "HEAD"], cwd=dest, timeout=REV_PARSE_TIMEOUT)
        if sha_result.returncode != 0:
            raise RepositoryRejected("clone", "could not read the cloned commit SHA")
    except BaseException:
        _rmtree(workdir)
        raise

    return ClonedRepo(path=dest, commit_sha=sha_result.stdout.strip(), workdir=workdir)
