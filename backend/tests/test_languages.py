"""The language-adapter architecture: detection, bounded source selection,
and each adapter's own safe verification.

Every fixture is a small, local, disposable git repository (tests.repo_
fixtures.local_repo) - no network anywhere in this file. Where a check needs
a real toolchain (py_compile, node --check, javac, gcc, g++) this suite uses
whatever is actually installed on the machine running it and asserts on the
REAL result; where a toolchain is unlikely to be present in CI (tsc, go,
cargo) causeway.languages._toolrun is mocked so the adapter's own DECISION
LOGIC is still verified deterministically, on any machine, regardless of
what happens to be on its PATH.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from causeway.languages import adapter_for, detect_languages
from causeway.languages import _toolrun
from causeway.languages.registry import is_denied_path
from causeway.patch.schema import PatchRequest
from causeway.patch.validator import validate as validate_patch
from causeway.repository import has_manifest
from causeway.repository.errors import RepositoryRejected
from causeway.repository.standard import discover_sources, load_standard
from causeway.repository.urlcheck import RepoRef
from causeway.repository import git as repogit
from tests.repo_fixtures import local_repo

REF = RepoRef(owner="o", name="n", url="https://github.com/o/n")


def _cloned(root):
    return repogit.ClonedRepo(path=root, commit_sha="a" * 40, workdir=root)


# --------------------------------------------------------------- fixtures --

PYTHON_FIXTURE = {
    "requirements.txt": "flask==3.0\n",
    "app.py": "def main():\n    return 1\n",
}

JS_FIXTURE = {
    "package.json": '{"name": "demo", "version": "1.0.0"}\n',
    "index.js": "function main() {\n  return 1;\n}\nmodule.exports = { main };\n",
}
JS_BROKEN = "function main() {\n  return 1;\n"   # missing closing brace

TS_FIXTURE = {
    "package.json": '{"name": "demo", "version": "1.0.0"}\n',
    "tsconfig.json": '{"compilerOptions": {"strict": true}}\n',
    "src/index.ts": "function main(): number {\n  return 1;\n}\nexport { main };\n",
}

JAVA_STDLIB_ONLY = (
    "import java.util.ArrayList;\n"
    "import java.util.List;\n\n"
    "public class Main {\n"
    "    public static void main(String[] args) {\n"
    "        List<String> items = new ArrayList<>();\n"
    "        items.add(\"hello\");\n"
    "        System.out.println(items);\n"
    "    }\n"
    "}\n"
)
JAVA_STDLIB_BROKEN = (
    "import java.util.List;\n\n"
    "public class Broken {\n"
    "    public static void main(String[] args) {\n"
    "        List<String> items =\n"          # missing the rest of the statement
    "    }\n"
    "}\n"
)
JAVA_EXTERNAL = (
    "import com.example.External;\n\n"
    "public class UsesExternal {\n"
    "    void run() { External.doThing(); }\n"
    "}\n"
)
JAVA_FIXTURE = {"pom.xml": "<project></project>\n", "Main.java": JAVA_STDLIB_ONLY}

GO_FIXTURE = {
    "go.mod": "module demo\n\ngo 1.21\n",
    "main.go": "package main\n\nfunc main() {\n\tprintln(\"hi\")\n}\n",
}

C_FIXTURE = {
    "Makefile": "all:\n\tgcc -o app main.c\n",
    "main.c": '#include <stdio.h>\nint main(void) {\n    printf("hello\\n");\n    return 0;\n}\n',
}
C_BROKEN = '#include <stdio.h>\nint main(void) {\n    printf("hello"\n    return 0;\n}\n'

CPP_FIXTURE = {
    "CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)\n",
    "main.cpp": '#include <iostream>\nint main() {\n    std::cout << "hello";\n    return 0;\n}\n',
}
CPP_BROKEN = '#include <iostream>\nint main() {\n    std::cout << "hello"\n    return 0;\n}\n'

CSHARP_FIXTURE = {"demo.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n",
                  "Program.cs": "class Program { static void Main() {} }\n"}

RUST_FIXTURE = {"Cargo.toml": "[package]\nname = \"demo\"\nversion = \"0.1.0\"\n",
                "src/main.rs": "fn main() {\n    println!(\"hi\");\n}\n"}


# --------------------------------------------------------------- detection --

class DetectionTests(unittest.TestCase):
    def test_python_is_detected_from_a_requirements_file_and_py_files(self):
        with local_repo(PYTHON_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "python")
        self.assertIn("python", detection.detected)

    def test_javascript_is_detected_from_package_json_and_js_files(self):
        with local_repo(JS_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "javascript")

    def test_typescript_is_detected_from_tsconfig_and_ts_files(self):
        with local_repo(TS_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "typescript")

    def test_java_is_detected_from_pom_xml_and_java_files(self):
        with local_repo(JAVA_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "java")

    def test_go_is_detected_from_go_mod_and_go_files(self):
        with local_repo(GO_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "go")

    def test_c_is_detected_from_makefile_and_c_files(self):
        with local_repo(C_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "c")

    def test_cpp_is_detected_from_cmakelists_and_cpp_files(self):
        with local_repo(CPP_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "cpp")

    def test_csharp_is_detected_from_a_csproj_file(self):
        with local_repo(CSHARP_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "csharp")

    def test_rust_is_detected_from_cargo_toml(self):
        with local_repo(RUST_FIXTURE) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "rust")

    def test_a_repository_with_no_recognised_signal_is_undetected(self):
        with local_repo({"README.md": "words", "notes.rb": "puts 1"}) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "")
        self.assertEqual(detection.detected, ())

    def test_a_mixed_repository_ranks_by_manifest_plus_file_count(self):
        files = dict(TS_FIXTURE)
        files["src/other.ts"] = "export const a = 1;\n"
        files["src/other2.ts"] = "export const b = 2;\n"
        files["legacy.js"] = "module.exports = 1;\n"
        with local_repo(files) as root:
            detection = detect_languages(root)
        self.assertEqual(detection.primary, "typescript")
        self.assertIn("javascript", detection.detected)
        self.assertGreater(detection.counts["typescript"], detection.counts["javascript"])

    def test_mixed_repositories_are_never_rejected_for_being_mixed(self):
        files = dict(PYTHON_FIXTURE)
        files.update(JS_FIXTURE)
        with local_repo(files) as root:
            ctx = load_standard(_cloned(root), REF)
        self.assertIn(ctx.primary_language, ("python", "javascript"))
        self.assertEqual(set(ctx.detected_languages), {"python", "javascript"})


# ----------------------------------------------------- causeway.json is optional

class NoManifestRequiredTests(unittest.TestCase):
    """The whole point of this feature: absence of causeway.json is never a
    rejection reason, for any supported language."""

    FIXTURES = {
        "python": PYTHON_FIXTURE, "javascript": JS_FIXTURE, "typescript": TS_FIXTURE,
        "java": JAVA_FIXTURE, "go": GO_FIXTURE, "c": C_FIXTURE, "cpp": CPP_FIXTURE,
    }

    def test_each_supported_language_loads_without_a_manifest(self):
        for language, files in self.FIXTURES.items():
            with self.subTest(language=language):
                with local_repo(files) as root:
                    self.assertFalse(has_manifest(root))
                    ctx = load_standard(_cloned(root), REF)
                self.assertEqual(ctx.primary_language, language)
                self.assertGreater(len(ctx.sources), 0)


class UnsupportedLanguageTests(unittest.TestCase):
    def test_an_unrecognised_language_is_rejected_by_analysis_naming_what_is_supported(self):
        with local_repo({"main.rb": "puts 'hi'\n"}) as root:
            with self.assertRaises(RepositoryRejected) as caught:
                load_standard(_cloned(root), REF)
        self.assertEqual(caught.exception.stage, "analysis")
        self.assertNotIn("causeway.json", caught.exception.reason)
        self.assertIn("Python", caught.exception.reason)


# ------------------------------------------------------------ source selection

class SourceSelectionTests(unittest.TestCase):
    def test_bounded_context_never_includes_skipped_directories(self):
        files = dict(JS_FIXTURE)
        files["node_modules/dep/index.js"] = "module.exports = 1;\n"
        files["dist/bundle.js"] = "console.log(1);\n"
        with local_repo(files) as root:
            chosen, _contents, all_files, _detection = discover_sources(root)
        for relative in chosen + all_files:
            self.assertNotIn("node_modules", relative)
            self.assertNotIn("dist/", relative)

    def test_env_and_credential_shaped_files_are_never_selected(self):
        files = dict(JS_FIXTURE)
        files[".env"] = "SECRET_KEY=abc123\n"
        files["config/credentials.js"] = "module.exports = { key: 'x' };\n"
        with local_repo(files) as root:
            chosen, contents, all_files, _detection = discover_sources(root)
        for relative in chosen + all_files + list(contents):
            self.assertFalse(is_denied_path(relative), relative)

    def test_selection_is_bounded_in_file_count(self):
        files = {"package.json": '{"name":"demo"}\n'}
        for i in range(30):
            files["src/file%d.js" % i] = "module.exports = %d;\n" % i
        with local_repo(files) as root:
            chosen, _contents, all_files, _detection = discover_sources(root)
        from causeway.repository.standard import MAX_FILES
        self.assertLessEqual(len(chosen), MAX_FILES)
        self.assertEqual(len(all_files), 30)

    def test_the_instruction_ranks_a_mentioned_file_into_the_selection(self):
        files = {"package.json": '{"name":"demo"}\n'}
        for i in range(20):
            files["src/unrelated%d.js" % i] = "module.exports = %d;\n" % i
        files["src/orders.js"] = "function chargeOrder() { return 1; }\n"
        with local_repo(files) as root:
            chosen, _contents, _all, _detection = discover_sources(
                root, "fix a bug in chargeOrder inside orders.js")
        self.assertIn("src/orders.js", chosen)

    def test_an_entrypoint_is_guessed_per_language(self):
        with local_repo(GO_FIXTURE) as root:
            ctx = load_standard(_cloned(root), REF)
        self.assertEqual(ctx.entrypoint, "main.go")

    def test_entrypoint_matching_is_case_insensitive(self):
        """Java's Main.java is capitalised by convention - entrypoint_names
        is written lowercase, and the match must not silently miss it."""
        with local_repo(JAVA_FIXTURE) as root:
            ctx = load_standard(_cloned(root), REF)
        self.assertEqual(ctx.entrypoint, "Main.java")


# ------------------------------------------------------ verification: real --
# These run the actual toolchain installed on this machine. python and node
# are assumed present (the project already depends on both); java/javac and
# a C/C++ compiler are common enough in CI images that a real assertion here
# is worth more than a mock - if one is genuinely absent, the "unavailable"
# assertions in the mocked section below still cover the decision logic.


class RealNodeVerificationTests(unittest.TestCase):
    def setUp(self):
        from causeway.languages._toolrun import which
        if not which("node"):
            self.skipTest("node is not installed on this machine")

    def test_valid_javascript_passes(self):
        with local_repo(JS_FIXTURE) as root:
            adapter = adapter_for("javascript")
            result = adapter.verify(root, ["index.js"])
        self.assertTrue(result.available)
        self.assertTrue(result.all_passed)

    def test_broken_javascript_fails(self):
        with local_repo({"index.js": JS_BROKEN}) as root:
            adapter = adapter_for("javascript")
            result = adapter.verify(root, ["index.js"])
        self.assertTrue(result.available)
        self.assertTrue(result.any_failed)


class RealJavacVerificationTests(unittest.TestCase):
    def setUp(self):
        from causeway.languages._toolrun import which
        if not which("javac"):
            self.skipTest("javac is not installed on this machine")

    def test_stdlib_only_java_compiles(self):
        with local_repo({"Main.java": JAVA_STDLIB_ONLY}) as root:
            result = adapter_for("java").verify(root, ["Main.java"])
        self.assertTrue(result.available)
        self.assertTrue(result.all_passed)

    def test_broken_stdlib_only_java_fails(self):
        with local_repo({"Broken.java": JAVA_STDLIB_BROKEN}) as root:
            result = adapter_for("java").verify(root, ["Broken.java"])
        self.assertTrue(result.available)
        self.assertTrue(result.any_failed)

    def test_java_needing_external_deps_is_reported_unavailable_not_failed(self):
        with local_repo({"UsesExternal.java": JAVA_EXTERNAL}) as root:
            result = adapter_for("java").verify(root, ["UsesExternal.java"])
        self.assertFalse(result.available)
        self.assertFalse(result.any_failed)
        self.assertIn("dependencies", result.note)


class RealCCompilerVerificationTests(unittest.TestCase):
    def setUp(self):
        from causeway.languages._toolrun import which
        if not (which("gcc") or which("cc")):
            self.skipTest("no C compiler is installed on this machine")

    def test_valid_c_passes(self):
        with local_repo(C_FIXTURE) as root:
            result = adapter_for("c").verify(root, ["main.c"])
        self.assertTrue(result.available)
        self.assertTrue(result.all_passed)

    def test_broken_c_fails(self):
        with local_repo({"main.c": C_BROKEN}) as root:
            result = adapter_for("c").verify(root, ["main.c"])
        self.assertTrue(result.available)
        self.assertTrue(result.any_failed)


class RealCppCompilerVerificationTests(unittest.TestCase):
    def setUp(self):
        from causeway.languages._toolrun import which
        if not (which("g++") or which("clang++")):
            self.skipTest("no C++ compiler is installed on this machine")

    def test_valid_cpp_passes(self):
        with local_repo(CPP_FIXTURE) as root:
            result = adapter_for("cpp").verify(root, ["main.cpp"])
        self.assertTrue(result.available)
        self.assertTrue(result.all_passed)

    def test_broken_cpp_fails(self):
        with local_repo({"main.cpp": CPP_BROKEN}) as root:
            result = adapter_for("cpp").verify(root, ["main.cpp"])
        self.assertTrue(result.available)
        self.assertTrue(result.any_failed)


class PythonVerificationTests(unittest.TestCase):
    def test_valid_python_passes(self):
        with local_repo(PYTHON_FIXTURE) as root:
            result = adapter_for("python").verify(root, ["app.py"])
        self.assertTrue(result.available)
        self.assertTrue(result.all_passed)

    def test_broken_python_fails(self):
        with local_repo({"app.py": "def broken(:\n    pass\n"}) as root:
            result = adapter_for("python").verify(root, ["app.py"])
        self.assertTrue(result.available)
        self.assertTrue(result.any_failed)


# --------------------------------------------------- verification: mocked --
# Go, Rust, C# and TypeScript's toolchains are unlikely to be on a CI
# machine, and cargo/go additionally require a network-resolved dependency
# graph to do anything at all - exactly the case this architecture refuses
# to attempt automatically. These tests mock causeway.languages._toolrun so
# the DECISION (never install, never fetch, report unavailable and say why)
# is verified independent of what happens to be installed here.

class MockedToolAbsentTests(unittest.TestCase):
    def test_go_without_the_toolchain_is_unavailable(self):
        with local_repo(GO_FIXTURE) as root:
            with mock.patch("causeway.languages._toolrun.which", return_value=None):
                result = adapter_for("go").verify(root, ["main.go"])
        self.assertFalse(result.available)
        self.assertIn("toolchain", result.note)

    def test_go_with_the_toolchain_but_no_vendor_dir_is_unavailable(self):
        with local_repo(GO_FIXTURE) as root:
            with mock.patch("causeway.languages._toolrun.which", return_value="/usr/bin/go"):
                result = adapter_for("go").verify(root, ["main.go"])
        self.assertFalse(result.available)
        self.assertIn("network", result.note)

    def test_go_with_a_vendored_module_graph_runs_vet(self):
        files = dict(GO_FIXTURE)
        files["vendor/.keep"] = ""
        with local_repo(files) as root:
            with mock.patch("causeway.languages._toolrun.which", return_value="/usr/bin/go"), \
                 mock.patch("causeway.languages._toolrun.run", return_value=(True, "")):
                result = adapter_for("go").verify(root, ["main.go"])
        self.assertTrue(result.available)
        self.assertTrue(result.all_passed)

    def test_rust_without_cargo_is_unavailable(self):
        with local_repo(RUST_FIXTURE) as root:
            with mock.patch("causeway.languages._toolrun.which", return_value=None):
                result = adapter_for("rust").verify(root, ["src/main.rs"])
        self.assertFalse(result.available)

    def test_rust_without_vendored_crates_is_unavailable(self):
        with local_repo(RUST_FIXTURE) as root:
            with mock.patch("causeway.languages._toolrun.which",
                            return_value="/usr/bin/cargo"):
                result = adapter_for("rust").verify(root, ["src/main.rs"])
        self.assertFalse(result.available)
        self.assertIn("network", result.note)

    def test_csharp_is_always_unavailable(self):
        with local_repo(CSHARP_FIXTURE) as root:
            result = adapter_for("csharp").verify(root, ["Program.cs"])
        self.assertFalse(result.available)
        self.assertIn("restore", result.note)

    def test_typescript_without_a_compiler_anywhere_is_unavailable(self):
        with local_repo(TS_FIXTURE) as root:
            with mock.patch("causeway.languages._toolrun.which", return_value=None):
                result = adapter_for("typescript").verify(root, ["src/index.ts"])
        self.assertFalse(result.available)

    def test_typescript_prefers_a_locally_installed_compiler(self):
        with local_repo(TS_FIXTURE) as root:
            bin_dir = os.path.join(root, "node_modules", ".bin")
            os.makedirs(bin_dir, exist_ok=True)
            shim_name = "tsc.cmd" if os.name == "nt" else "tsc"
            shim_path = os.path.join(bin_dir, shim_name)
            with open(shim_path, "w") as handle:
                handle.write("")
            with mock.patch("causeway.languages._toolrun.which",
                            return_value="/usr/bin/tsc"), \
                 mock.patch("causeway.languages._toolrun.run",
                           return_value=(True, "")) as run:
                result = adapter_for("typescript").verify(root, ["src/index.ts"])
            self.assertTrue(result.available)
            self.assertTrue(result.all_passed)
            used_tool = run.call_args.args[0][0]
            self.assertEqual(used_tool, shim_path)

    def test_no_adapter_ever_invokes_an_install_or_fetch_command(self):
        """Static source audit: every actual argv array any adapter builds
        (every call to _toolrun.run) is free of an install/fetch
        subcommand. Scoped to the argv literals themselves, not the module's
        prose - a docstring explaining why dotnet restore is NOT run must
        not trip a scan for the words "dotnet restore"."""
        import ast
        import inspect

        import causeway.languages.adapters as adapters_module
        source = inspect.getsource(adapters_module)
        tree = ast.parse(source)
        forbidden = ("install", "restore", "get", "fetch", "apt-get")
        argv_calls = 0
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run" and node.args
                    and isinstance(node.args[0], ast.List)):
                argv_calls += 1
                for element in node.args[0].elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        for word in forbidden:
                            self.assertNotIn(word, element.value.lower())
        self.assertGreater(argv_calls, 0, "no _toolrun.run(...) call sites were found at all")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


# -------------------------------------------------------- patch validation --
# The patch validator is language-agnostic already (it only ever compares
# text and paths); these prove it was never weakened by this change, for a
# non-Python language.

class PatchValidationStillEnforcedTests(unittest.TestCase):
    def _request_for(self, root, sources, patchable, contents):
        return PatchRequest(
            instruction="fix it", goal="fix it", intent={}, service="demo",
            entrypoint="", sources=tuple(sources), patchable=tuple(patchable),
            file_contents=contents, acceptance={})

    def test_a_go_patch_with_a_path_traversal_target_is_rejected(self):
        with local_repo(GO_FIXTURE) as root:
            request = self._request_for(root, ["main.go"], ["main.go"],
                                        {"main.go": GO_FIXTURE["main.go"]})
            raw = {"summary": "s", "reasoning_summary": "r",
                  "files": [{"path": "../outside.go",
                            "hunks": [{"before": "package main", "after": "package evil"}]}]}
            report = validate_patch(raw, request, root)
        self.assertFalse(report.accepted)

    def test_a_go_patch_touching_env_is_rejected_even_if_offered(self):
        with local_repo(GO_FIXTURE) as root:
            with open(os.path.join(root, ".env"), "w") as handle:
                handle.write("SECRET=1\n")
            request = self._request_for(
                root, ["main.go", ".env"], ["main.go", ".env"],
                {"main.go": GO_FIXTURE["main.go"], ".env": "SECRET=1\n"})
            raw = {"summary": "s", "reasoning_summary": "r",
                  "files": [{"path": ".env",
                            "hunks": [{"before": "SECRET=1\n", "after": "SECRET=2\n"}]}]}
            report = validate_patch(raw, request, root)
        self.assertFalse(report.accepted)

    def test_a_well_formed_go_patch_is_accepted(self):
        with local_repo(GO_FIXTURE) as root:
            before = GO_FIXTURE["main.go"]
            after = before.replace('println("hi")', 'println("bye")')
            request = self._request_for(root, ["main.go"], ["main.go"], {"main.go": before})
            raw = {"summary": "s", "reasoning_summary": "r",
                  "files": [{"path": "main.go", "hunks": [{"before": before, "after": after}]}]}
            report = validate_patch(raw, request, root)
        self.assertTrue(report.accepted)


# ------------------------------------------------- full orchestrator, non-Python

@unittest.skipUnless(_toolrun.which("node"), "node is not installed on this machine")
class NonPythonStandardRepositoryEndToEndTests(unittest.TestCase):
    """The same lifecycle tests/test_standard_repository.py proves for
    Python, run once for a different language end to end through the real
    orchestrator - repository acquisition, language detection, bounded
    source selection, a mocked-but-realistic Gemini patch, the real
    (unmocked) patch validator, a disposable copy, and node's own real
    --check verifying the result."""

    BROKEN_JS = "function total(items) {\n  retrun items.length;\n}\nmodule.exports = { total };\n"
    FIXED_JS = "function total(items) {\n  return items.length;\n}\nmodule.exports = { total };\n"

    @classmethod
    def setUpClass(cls):
        from causeway import orchestrator
        from causeway.patch.gemini import GeminiPatchPlanner
        from causeway.repository import git as repogit

        def _cloning_from(local_source):
            def _clone(ref, timeout=repogit.CLONE_TIMEOUT, source=None):
                return repogit.clone(ref, timeout=timeout, source=local_source)
            return _clone

        def _envelope(patch):
            import json
            return {"candidates": [{"content": {"role": "model",
                                                "parts": [{"text": json.dumps(patch)}]}}]}

        with local_repo({"package.json": JS_FIXTURE["package.json"],
                         "index.js": cls.BROKEN_JS}) as source:
            patch = {
                "summary": "Fix the typo'd return keyword",
                "files": [{"path": "index.js",
                          "hunks": [{"before": cls.BROKEN_JS, "after": cls.FIXED_JS}]}],
                "reasoning_summary": "retrun was a typo for return.",
            }
            transport = mock.Mock(return_value=_envelope(patch))
            with mock.patch("causeway.patch.gemini.api_key_from_env",
                            return_value="fake-test-key"), \
                 mock.patch.object(GeminiPatchPlanner, "_post", transport), \
                 mock.patch("causeway.repository.clone", _cloning_from(source)):
                cls.events = list(orchestrator.investigate(
                    repository_url="https://github.com/o/n", offline=False,
                    instruction="fix the typo in total() and explain the change",
                    mode="diagnose_and_fix"))

    def _of(self, event_type):
        return [e for e in self.events if e["type"] == event_type]

    def test_no_rejection_and_a_standard_contract_naming_javascript(self):
        self.assertEqual(self._of("repository_rejected"), [])
        loaded = self._of("repository_loaded")[0]
        self.assertEqual(loaded["contract"], "standard")
        self.assertEqual(loaded["primary_language"], "javascript")

    def test_language_detected_and_source_selection_events_fire(self):
        detected = self._of("language_detected")[0]
        self.assertEqual(detected["primary"], "javascript")
        selection = self._of("source_selection")[0]
        self.assertIn("index.js", selection["files"])

    def test_the_patch_is_validated_and_applied(self):
        self.assertTrue(self._of("patch_validation")[0]["accepted"])
        applied = self._of("patch_apply")[0]
        self.assertIn("-  retrun items.length;", applied["diff"])
        self.assertIn("+  return items.length;", applied["diff"])

    def test_verification_ran_with_node_and_the_verdict_is_honest(self):
        checks = self._of("verification_check")
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["language"], "javascript")
        self.assertEqual(checks[0]["tool"], "node --check")
        self.assertTrue(checks[0]["passed"])
        verdict = self._of("requested_change_verdict")[0]
        self.assertEqual(verdict["verdict"], "IMPLEMENTED_VERIFICATION_INCOMPLETE")

    def test_the_run_ends_cleanly(self):
        self.assertEqual(self.events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
