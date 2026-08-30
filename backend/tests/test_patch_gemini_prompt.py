"""causeway.patch.gemini.build_prompt: the OBSERVED RUNTIME BEHAVIOUR
section, present only when causeway.languages.python_runtime actually
produced evidence for this request - never a placeholder, never present for
a request that never ran anything.
"""
from __future__ import annotations

import unittest

from causeway.patch.gemini import SYSTEM_INSTRUCTION, build_prompt
from causeway.patch.schema import PatchRequest


def request_for(runtime_evidence: str = "") -> PatchRequest:
    return PatchRequest(
        instruction="fix the bug", goal="fix the bug", intent={}, service="demo",
        entrypoint="app.py", sources=("app.py",), patchable=("app.py",),
        file_contents={"app.py": "def broken():\n    pass\n"}, acceptance={},
        runtime_evidence=runtime_evidence)


class RuntimeEvidenceSectionTests(unittest.TestCase):
    def test_no_evidence_means_no_section_at_all(self):
        prompt = build_prompt(request_for(runtime_evidence=""))
        self.assertNotIn("OBSERVED RUNTIME BEHAVIOUR", prompt)

    def test_real_evidence_appears_verbatim(self):
        evidence = "entrypoint: app.py\nIndexError: list index out of range (app.py:3 in inner())"
        prompt = build_prompt(request_for(runtime_evidence=evidence))
        self.assertIn("OBSERVED RUNTIME BEHAVIOUR", prompt)
        self.assertIn(evidence, prompt)

    def test_the_section_appears_before_the_files_section(self):
        evidence = "entrypoint: app.py\nran to completion in 0.01s with exit code 0"
        prompt = build_prompt(request_for(runtime_evidence=evidence))
        self.assertLess(prompt.index("OBSERVED RUNTIME BEHAVIOUR"),
                        prompt.index("FILES YOU MAY EDIT"))

    def test_the_section_is_labelled_as_real_not_inferred(self):
        prompt = build_prompt(request_for(runtime_evidence="ran cleanly"))
        self.assertIn("real, captured evidence", prompt)


class SystemInstructionTests(unittest.TestCase):
    def test_the_system_instruction_explains_what_runtime_evidence_is_and_is_not(self):
        self.assertIn("OBSERVED RUNTIME BEHAVIOUR", SYSTEM_INSTRUCTION)
        self.assertIn("not as proof your fix is complete", SYSTEM_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
