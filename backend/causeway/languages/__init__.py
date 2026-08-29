"""Language adapters for the standard (manifest-less) repository path.

causeway.repository.standard used to know only Python. This package pulls
that knowledge out into one small, uniform adapter per language, so adding a
language is adding a module here - never touching the walking, scoring,
bounding or patch-application logic in causeway.repository.standard or
causeway.standard_investigation, all of which stay language-agnostic.

An adapter answers three questions and nothing else:

    which files, by extension, are this language's source
    (matches_file)
    which files, by name, mean "this project is written in this language"
    (manifest_files - requirements.txt, package.json, go.mod, ...)
    given a disposable, already-patched copy of the repository and the
    files a patch touched, what CHEAP, NON-EXECUTING or LOW-RISK check can
    be run to know whether the patch is at least syntactically sound
    (verify)

No adapter runs the repository's own application, installs a dependency, or
executes a build script from the repository. See base.py's LanguageAdapter
docstring for the exact contract every adapter must honour.
"""
from __future__ import annotations

from causeway.languages.base import (LanguageAdapter, VerificationCheck,
                                     VerificationResult)
from causeway.languages.registry import (ADAPTERS, LanguageDetection,
                                         adapter_for, detect_languages)

__all__ = ["LanguageAdapter", "VerificationCheck", "VerificationResult",
          "ADAPTERS", "LanguageDetection", "adapter_for", "detect_languages"]
