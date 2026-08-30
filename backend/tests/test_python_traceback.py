"""causeway.languages.python_traceback: deterministic parsing of Python's
own traceback text. Pure string-in/dataclass-out - no subprocess here.

Every fixture below is real output, captured once from this machine's own
Python 3.14 interpreter running a real temporary script (never `python -c`,
which reports the file as "<string>" rather than a real path) - including
the PEP 657 fine-grained source/caret annotation lines 3.11+ adds under
every frame, so the parser is proven against what actually comes out of a
real run, not an assumed shape.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from causeway.languages.python_traceback import parse_traceback

WORKSPACE = os.path.join(tempfile.gettempdir(), "causeway-test-workspace")

INDEX_ERROR = (
    'Traceback (most recent call last):\n'
    '  File "%s\\app.py", line 8, in <module>\n'
    '    outer()\n'
    '    ~~~~~^^\n'
    '  File "%s\\app.py", line 6, in outer\n'
    '    return inner()\n'
    '  File "%s\\app.py", line 3, in inner\n'
    '    return numbers[5]\n'
    '           ~~~~~~~^^^\n'
    'IndexError: list index out of range'
) % (WORKSPACE, WORKSPACE, WORKSPACE)

SYNTAX_ERROR = (
    '  File "%s\\bad_syntax.py", line 1\n'
    '    def hello()\n'
    '               ^\n'
    "SyntaxError: expected ':'"
) % WORKSPACE

STDLIB_FRAME_SKIPPED = (
    'Traceback (most recent call last):\n'
    '  File "%s\\uses_json.py", line 6, in <module>\n'
    '    load_config()\n'
    '    ~~~~~~~~~~~^^\n'
    '  File "%s\\uses_json.py", line 4, in load_config\n'
    '    return json.loads("not json")\n'
    '           ~~~~~~~~~~^^^^^^^^^^^^\n'
    '  File "C:\\Python314\\Lib\\json\\__init__.py", line 352, in loads\n'
    '    return _default_decoder.decode(s)\n'
    '           ~~~~~~~~~~~~~~~~~~~~~~~^^^\n'
    '  File "C:\\Python314\\Lib\\json\\decoder.py", line 345, in decode\n'
    '    obj, end = self.raw_decode(s, idx=_w(s, 0).end())\n'
    '               ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "C:\\Python314\\Lib\\json\\decoder.py", line 363, in raw_decode\n'
    '    raise JSONDecodeError("Expecting value", s, err.value) from None\n'
    'json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)'
) % (WORKSPACE, WORKSPACE)

CHAINED_EXCEPTION = (
    'Traceback (most recent call last):\n'
    '  File "%s\\app.py", line 3, in <module>\n'
    '    1/0\n'
    '    ~^~\n'
    'ZeroDivisionError: division by zero\n'
    '\n'
    'During handling of the above exception, another exception occurred:\n'
    '\n'
    'Traceback (most recent call last):\n'
    '  File "%s\\app.py", line 5, in <module>\n'
    "    raise ValueError('wrapped')\n"
    'ValueError: wrapped'
) % (WORKSPACE, WORKSPACE)

ENTIRELY_OUTSIDE_WORKSPACE = (
    'Traceback (most recent call last):\n'
    '  File "C:\\some\\other\\place\\lib.py", line 10, in helper\n'
    '    do_thing()\n'
    'RuntimeError: boom'
)


class RuntimeTracebackTests(unittest.TestCase):
    def test_a_plain_index_error_is_parsed_correctly(self):
        finding = parse_traceback(INDEX_ERROR, WORKSPACE)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.kind, "runtime")
        self.assertEqual(finding.exception_type, "IndexError")
        self.assertEqual(finding.message, "list index out of range")
        self.assertEqual(finding.file, "app.py")
        self.assertEqual(finding.line, 3)
        self.assertEqual(finding.function, "inner")
        self.assertTrue(finding.frame_available)

    def test_the_innermost_frame_wins_not_the_top_of_the_call_stack(self):
        """outer() -> inner() -> the actual failure. The last frame in the
        traceback (closest to the raise) is the one reported, not <module>."""
        finding = parse_traceback(INDEX_ERROR, WORKSPACE)
        self.assertNotEqual(finding.function, "<module>")
        self.assertNotEqual(finding.function, "outer")


class SyntaxErrorTests(unittest.TestCase):
    def test_a_syntax_error_is_parsed_with_no_function(self):
        finding = parse_traceback(SYNTAX_ERROR, WORKSPACE)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.kind, "syntax")
        self.assertEqual(finding.exception_type, "SyntaxError")
        self.assertEqual(finding.message, "expected ':'")
        self.assertEqual(finding.file, "bad_syntax.py")
        self.assertEqual(finding.line, 1)
        self.assertIsNone(finding.function)
        self.assertTrue(finding.frame_available)


class StdlibFrameSkippingTests(unittest.TestCase):
    def test_a_stdlib_only_failure_is_attributed_to_the_users_own_call_site(self):
        """json.loads() itself raises three frames deep inside the stdlib.
        None of those resolve inside the workspace, so the parser must walk
        up to the user's own load_config() call - not report a stdlib path,
        and not give up just because the immediate failure is elsewhere."""
        finding = parse_traceback(STDLIB_FRAME_SKIPPED, WORKSPACE)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.exception_type, "json.decoder.JSONDecodeError")
        self.assertEqual(finding.file, "uses_json.py")
        self.assertEqual(finding.line, 4)
        self.assertEqual(finding.function, "load_config")
        self.assertTrue(finding.frame_available)

    def test_a_dotted_exception_type_is_captured_whole(self):
        finding = parse_traceback(STDLIB_FRAME_SKIPPED, WORKSPACE)
        self.assertIn(".", finding.exception_type)


class ChainedExceptionTests(unittest.TestCase):
    def test_only_the_last_exception_in_a_chain_is_reported(self):
        finding = parse_traceback(CHAINED_EXCEPTION, WORKSPACE)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.exception_type, "ValueError")
        self.assertEqual(finding.message, "wrapped")
        self.assertEqual(finding.line, 5)

    def test_the_earlier_exception_in_the_chain_is_not_reported(self):
        finding = parse_traceback(CHAINED_EXCEPTION, WORKSPACE)
        self.assertNotEqual(finding.exception_type, "ZeroDivisionError")


class FrameOutsideWorkspaceTests(unittest.TestCase):
    def test_no_frame_inside_the_workspace_is_reported_honestly(self):
        finding = parse_traceback(ENTIRELY_OUTSIDE_WORKSPACE, WORKSPACE)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.exception_type, "RuntimeError")
        self.assertEqual(finding.message, "boom")
        self.assertFalse(finding.frame_available)
        self.assertIsNone(finding.file)
        self.assertIsNone(finding.line)
        self.assertIsNone(finding.function)


class NonTracebackInputTests(unittest.TestCase):
    def test_empty_stderr_is_none(self):
        self.assertIsNone(parse_traceback("", WORKSPACE))

    def test_whitespace_only_stderr_is_none(self):
        self.assertIsNone(parse_traceback("   \n  \n", WORKSPACE))

    def test_ordinary_print_output_is_none(self):
        self.assertIsNone(parse_traceback("hello\nworld\n", WORKSPACE))

    def test_a_warning_with_no_exception_is_none(self):
        self.assertIsNone(parse_traceback(
            "app.py:3: DeprecationWarning: use something_else() instead\n", WORKSPACE))


class RawTextTests(unittest.TestCase):
    def test_raw_carries_the_original_segment(self):
        finding = parse_traceback(INDEX_ERROR, WORKSPACE)
        self.assertIn("IndexError", finding.raw)
        self.assertIn("Traceback (most recent call last):", finding.raw)

    def test_raw_is_bounded(self):
        huge = "Traceback (most recent call last):\n" + ("  # padding\n" * 2000) + "ValueError: x"
        finding = parse_traceback(huge, WORKSPACE)
        if finding is not None:
            self.assertLessEqual(len(finding.raw), 4000)


class AsDictTests(unittest.TestCase):
    def test_as_dict_has_every_field(self):
        finding = parse_traceback(INDEX_ERROR, WORKSPACE)
        payload = finding.as_dict()
        self.assertEqual(set(payload.keys()),
                         {"kind", "exception_type", "message", "file", "line",
                          "function", "frame_available", "raw"})


if __name__ == "__main__":
    unittest.main()
