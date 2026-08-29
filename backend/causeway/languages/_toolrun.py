"""One safe way for an adapter to run a locally-installed compiler's syntax
check - never a shell, never the repository's own scripts.

Every call here is an argv array, resolved through shutil.which first so an
adapter never even attempts a tool that is not actually on this machine's
PATH, and every call has a short timeout so one hung process cannot stall an
investigation. Nothing here is a repository-provided command: the tool name
and every flag come from the adapter's own source, never from the
repository or from Gemini.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional, Sequence, Tuple

DEFAULT_TIMEOUT = 20.0


def which(tool: str) -> Optional[str]:
    return shutil.which(tool)


def run(argv: Sequence[str], cwd: str, timeout: float = DEFAULT_TIMEOUT
       ) -> Tuple[bool, str]:
    """Run one local tool, as an argv array, never a shell string.

    Returns (ok, output). ok is True only on exit code 0; a nonzero exit,
    a timeout, or the tool not existing all come back as (False, detail) -
    never an exception a caller has to remember to catch.
    """
    try:
        result = subprocess.run(
            list(argv), cwd=cwd, timeout=timeout, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=False)
    except FileNotFoundError:
        return False, "%s is not available on this machine" % argv[0]
    except subprocess.TimeoutExpired:
        return False, "%s did not finish within %.0fs" % (argv[0], timeout)
    except OSError as exc:
        return False, "%s could not be run: %s" % (argv[0], exc)

    output = (result.stdout or "").strip()
    return result.returncode == 0, output
