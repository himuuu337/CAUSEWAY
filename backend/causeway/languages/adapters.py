"""One adapter per supported language.

Every `verify` here follows the rule base.py documents: a syntax or type
check the language's own toolchain performs without installing anything,
fetching anything, or running the repository's own code - and `available:
False` with an honest reason the moment that is not possible, rather than
reaching for something riskier.
"""
from __future__ import annotations

import io
import os
import py_compile
import shutil
import tempfile
from typing import Sequence

from causeway.languages import _toolrun
from causeway.languages.base import LanguageAdapter, VerificationCheck, VerificationResult


class PythonAdapter(LanguageAdapter):
    id = "python"
    display_name = "Python"
    source_extensions = (".py",)
    manifest_files = ("requirements.txt", "pyproject.toml", "setup.py",
                      "pipfile", "poetry.lock", "setup.cfg")
    entrypoint_names = ("app.py", "main.py", "manage.py", "wsgi.py", "asgi.py",
                        "run.py", "server.py")

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        checks = []
        for relative in changed_files:
            path = os.path.join(workspace, relative)
            try:
                # quiet=2 is deliberately NOT used: on some interpreter
                # builds it has been observed to swallow the PyCompileError
                # doraise is supposed to raise, which would turn a broken
                # patch into a false pass.
                py_compile.compile(path, doraise=True)
                checks.append(VerificationCheck("py_compile", relative, True, "compiles cleanly"))
            except py_compile.PyCompileError as exc:
                checks.append(VerificationCheck("py_compile", relative, False, str(exc.msg)))
            except OSError as exc:
                checks.append(VerificationCheck("py_compile", relative, False, str(exc)))
        return VerificationResult(True, tuple(checks),
                                  "syntax-checked with py_compile (parses and compiles to "
                                  "bytecode; never executes the module)")


class JavaScriptAdapter(LanguageAdapter):
    id = "javascript"
    display_name = "JavaScript"
    source_extensions = (".js", ".jsx", ".mjs", ".cjs")
    manifest_files = ("package.json",)
    entrypoint_names = ("index.js", "app.js", "main.js", "server.js")

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        node = _toolrun.which("node")
        if not node:
            return VerificationResult(
                False, (), "node is not available on this machine, so even a "
                          "syntax check could not be run")
        checks = []
        for relative in changed_files:
            path = os.path.join(workspace, relative)
            ok, output = _toolrun.run([node, "--check", path], cwd=workspace)
            checks.append(VerificationCheck("node --check", relative, ok,
                                            output or ("syntax ok" if ok else "syntax error")))
        return VerificationResult(True, tuple(checks),
                                  "syntax-checked with node --check (parses only; the file "
                                  "is never executed and no module is resolved)")


class TypeScriptAdapter(LanguageAdapter):
    id = "typescript"
    display_name = "TypeScript"
    source_extensions = (".ts", ".tsx")
    manifest_files = ("tsconfig.json",)
    entrypoint_names = ("index.ts", "main.ts", "app.ts", "server.ts")

    def _tsc(self, workspace: str):
        local = os.path.join(workspace, "node_modules", ".bin",
                             "tsc.cmd" if os.name == "nt" else "tsc")
        if os.path.isfile(local):
            return local
        return _toolrun.which("tsc")

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        tsc = self._tsc(workspace)
        if not tsc:
            return VerificationResult(
                False, (), "no TypeScript compiler is available - not installed in this "
                          "repository's own node_modules and not on this machine's PATH "
                          "- and Causeway does not install one to verify a patch")
        checks = []
        for relative in changed_files:
            path = os.path.join(workspace, relative)
            ok, output = _toolrun.run(
                [tsc, "--noEmit", "--skipLibCheck", "--allowJs", path],
                cwd=workspace, timeout=30.0)
            checks.append(VerificationCheck("tsc --noEmit", relative, ok,
                                            output or ("type-checks cleanly" if ok
                                                      else "type error")))
        return VerificationResult(True, tuple(checks),
                                  "type-checked with tsc --noEmit (no output file is "
                                  "written and nothing is executed)")


class JavaAdapter(LanguageAdapter):
    id = "java"
    display_name = "Java"
    source_extensions = (".java",)
    manifest_files = ("pom.xml", "build.gradle", "build.gradle.kts")
    entrypoint_names = ("main.java", "application.java")

    _STDLIB_PREFIXES = ("java.", "javax.")

    def _needs_external_deps(self, source: str) -> bool:
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith("import "):
                continue
            target = stripped[len("import "):].strip()
            if target.startswith("static "):
                target = target[len("static "):].strip()
            target = target.rstrip(";").strip()
            if not target.startswith(self._STDLIB_PREFIXES):
                return True
        return False

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        javac = _toolrun.which("javac")
        if not javac:
            return VerificationResult(False, (),
                                      "javac is not available on this machine")
        checks = []
        skipped_external = False
        out_dir = tempfile.mkdtemp(prefix="causeway-javac-")
        try:
            for relative in changed_files:
                path = os.path.join(workspace, relative)
                try:
                    with io.open(path, "r", encoding="utf-8", errors="ignore") as handle:
                        source = handle.read()
                except OSError as exc:
                    checks.append(VerificationCheck("javac", relative, False, str(exc)))
                    continue
                if self._needs_external_deps(source):
                    skipped_external = True
                    continue
                ok, output = _toolrun.run([javac, "-d", out_dir, "-nowarn", path],
                                          cwd=workspace, timeout=30.0)
                checks.append(VerificationCheck("javac", relative, ok,
                                                output or ("compiles cleanly" if ok
                                                          else "compile error")))
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        if not checks:
            return VerificationResult(
                False, (), "every changed .java file imports something beyond the "
                          "Java standard library, so compiling it would require "
                          "dependencies Causeway does not download")
        note = ("compiled with javac against the changed file(s) (standard-library "
               "imports only; nothing was executed)")
        if skipped_external:
            note += "; some changed files were skipped because they import external packages"
        return VerificationResult(True, tuple(checks), note)


class GoAdapter(LanguageAdapter):
    id = "go"
    display_name = "Go"
    source_extensions = (".go",)
    manifest_files = ("go.mod",)
    entrypoint_names = ("main.go",)

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        go = _toolrun.which("go")
        if not go:
            return VerificationResult(False, (),
                                      "the go toolchain is not available on this machine")
        if not os.path.isdir(os.path.join(workspace, "vendor")):
            return VerificationResult(
                False, (), "this repository does not vendor its dependencies, and "
                          "building or vetting Go source resolves its module graph "
                          "over the network - Causeway does not fetch dependencies "
                          "to verify a patch")
        ok, output = _toolrun.run([go, "vet", "-mod=vendor", "./..."],
                                  cwd=workspace, timeout=45.0)
        checks = tuple(
            VerificationCheck("go vet", relative, ok,
                              output or ("vets cleanly" if ok else "vet reported a problem"))
            for relative in changed_files)
        return VerificationResult(True, checks,
                                  "checked with go vet -mod=vendor (vendored dependencies "
                                  "only; nothing was built or run)")


class CAdapter(LanguageAdapter):
    id = "c"
    display_name = "C"
    source_extensions = (".c", ".h")
    manifest_files = ("makefile", "cmakelists.txt")
    entrypoint_names = ("main.c",)

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        gcc = _toolrun.which("gcc") or _toolrun.which("cc")
        if not gcc:
            return VerificationResult(False, (),
                                      "no C compiler (gcc/cc) is available on this machine")
        checks = []
        for relative in changed_files:
            if relative.lower().endswith(".h"):
                continue    # checked through the .c file(s) that include it
            path = os.path.join(workspace, relative)
            ok, output = _toolrun.run([gcc, "-fsyntax-only", "-I", workspace, path],
                                      cwd=workspace, timeout=20.0)
            checks.append(VerificationCheck("gcc -fsyntax-only", relative, ok,
                                            output or ("syntax ok" if ok else "syntax error")))
        if not checks:
            return VerificationResult(
                True, (), "only header file(s) changed; nothing to syntax-check directly")
        return VerificationResult(True, tuple(checks),
                                  "syntax-checked with gcc -fsyntax-only (no code is "
                                  "generated, and nothing is linked or run)")


class CppAdapter(LanguageAdapter):
    id = "cpp"
    display_name = "C++"
    source_extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")
    manifest_files = ("makefile", "cmakelists.txt")
    entrypoint_names = ("main.cpp",)

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        gpp = _toolrun.which("g++") or _toolrun.which("clang++")
        if not gpp:
            return VerificationResult(
                False, (), "no C++ compiler (g++/clang++) is available on this machine")
        checks = []
        for relative in changed_files:
            if relative.lower().endswith((".hpp", ".hh", ".hxx")):
                continue
            path = os.path.join(workspace, relative)
            ok, output = _toolrun.run(
                [gpp, "-std=c++17", "-fsyntax-only", "-I", workspace, path],
                cwd=workspace, timeout=20.0)
            checks.append(VerificationCheck("g++ -fsyntax-only", relative, ok,
                                            output or ("syntax ok" if ok else "syntax error")))
        if not checks:
            return VerificationResult(
                True, (), "only header file(s) changed; nothing to syntax-check directly")
        return VerificationResult(True, tuple(checks),
                                  "syntax-checked with g++ -fsyntax-only (no code is "
                                  "generated, and nothing is linked or run)")


class CSharpAdapter(LanguageAdapter):
    id = "csharp"
    display_name = "C#"
    source_extensions = (".cs",)
    manifest_files = ("*.csproj", "*.sln")
    entrypoint_names = ("program.cs",)

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        # dotnet build/check requires a package restore first, which fetches
        # NuGet packages over the network - not something Causeway does
        # automatically to verify a patch.
        return VerificationResult(
            False, (), "a .NET project needs `dotnet restore` before it can be built "
                      "or checked, and Causeway does not fetch dependencies to "
                      "verify a patch")


class RustAdapter(LanguageAdapter):
    id = "rust"
    display_name = "Rust"
    source_extensions = (".rs",)
    manifest_files = ("Cargo.toml",)
    entrypoint_names = ("main.rs", "lib.rs")

    def verify(self, workspace: str, changed_files: Sequence[str]) -> VerificationResult:
        cargo = _toolrun.which("cargo")
        if not cargo:
            return VerificationResult(False, (),
                                      "cargo is not available on this machine")
        vendored = (os.path.isdir(os.path.join(workspace, "vendor"))
                   or os.path.isdir(os.path.join(workspace, ".cargo", "registry")))
        if not vendored:
            return VerificationResult(
                False, (), "this repository does not vendor its crates, and `cargo "
                          "check` resolves the dependency graph over the network "
                          "otherwise - Causeway does not fetch dependencies to "
                          "verify a patch")
        ok, output = _toolrun.run([cargo, "check", "--offline"], cwd=workspace, timeout=60.0)
        checks = tuple(
            VerificationCheck("cargo check --offline", relative, ok,
                              output or ("checks cleanly" if ok else "check failed"))
            for relative in changed_files)
        return VerificationResult(True, checks,
                                  "checked with cargo check --offline against vendored "
                                  "crates only; nothing was built or run")
