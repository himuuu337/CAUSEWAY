"""Shared helpers for repository-ingestion tests: build a disposable local
git repository from a dict of files (or from the real demo-repo/ directory),
without depending on live GitHub or the network anywhere in this file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager

DEMO_REPO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "demo-repo")


def write_files(root: str, files: dict) -> None:
    for relative, content in files.items():
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        if isinstance(content, bytes):
            with open(path, "wb") as handle:
                handle.write(content)
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def init_git_repo(root: str) -> str:
    """git init + commit everything in `root`. Returns the HEAD sha."""
    _run(["git", "-c", "core.autocrlf=false", "init", "-q"], root)
    _run(["git", "-c", "core.autocrlf=false", "add", "-A"], root)
    _run(["git", "-c", "core.autocrlf=false", "-c", "user.email=test@causeway.local",
         "-c", "user.name=causeway-tests", "commit", "-q", "-m", "test"], root)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


@contextmanager
def local_repo(files: dict = None, copy_demo: bool = False):
    """A disposable local git repository, cleaned up on exit.

    `copy_demo=True` seeds it from the real demo-repo/ directory (the actual
    Causeway demo contract) before `files` is applied on top, so a test can
    start from a known-good repository and corrupt exactly one thing.
    """
    root = tempfile.mkdtemp(prefix="causeway-test-repo-")
    try:
        if copy_demo:
            for name in os.listdir(DEMO_REPO_DIR):
                source = os.path.join(DEMO_REPO_DIR, name)
                dest = os.path.join(root, name)
                if os.path.isdir(source):
                    shutil.copytree(source, dest)
                else:
                    shutil.copyfile(source, dest)
        if files:
            write_files(root, files)
        init_git_repo(root)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
