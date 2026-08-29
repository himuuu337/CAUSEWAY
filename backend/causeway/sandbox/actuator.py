"""How a phase's state is put into effect.

The seven-phase protocol asks for one thing per phase: "make this set of
candidates present, then measure". There are two honest ways to do that, and
this module is where they stop being the orchestrator's problem.

    FlagActuator    the bundled demo. One long-lived service that exposes a
                    runtime switch; a phase POSTs the switch positions.

    SourceActuator  a repository. There is no switch and there should not be:
                    the candidates ARE places in the source. A phase copies
                    the workspace, writes the counterfactual for every
                    candidate that must be absent, starts the service against
                    that copy, measures it, and throws the copy away.

Both return the same signature dict, so causeway.verdict cannot tell which
one ran - which is the point. The measurement engine and the verdict engine
are untouched by any of this.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from causeway.sandbox.runner import Sandbox
from causeway.sandbox.variant import SourceEdit, materialise


class FlagActuator:
    """The bundled demo's actuator: one service, flags toggled per phase."""

    kind = "runtime_flags"

    def __init__(self, template_db: str, work_db: str, service_path: str = None):
        self._sandbox = Sandbox(template_db, work_db, service_path=service_path)

    def __enter__(self):
        self._sandbox.start()
        return self

    def __exit__(self, *exc):
        self._sandbox.stop()
        return False

    def measure(self, fixture: dict, flags: Mapping[str, bool], reps: int,
                extra_edits: Sequence = ()) -> dict:
        if extra_edits:                    # a flag actuator has no source to edit
            raise TypeError("FlagActuator cannot apply source edits")
        return self._sandbox.measure(fixture, dict(flags), reps)

    def describe(self, flags: Mapping[str, bool], extra_edits: Sequence = ()) -> dict:
        return {"kind": self.kind, "flags": dict(flags)}


class SourceActuator:
    """A repository's actuator: one disposable source variant per phase.

    Nothing is ever written to the cloned workspace. Every phase gets its own
    copy, and the copy is removed whether the phase succeeded or raised.
    """

    kind = "source_variant"

    def __init__(self, workspace: str, entrypoint: str, database: str,
                 work_db: str, hypotheses: Sequence):
        self.workspace = workspace
        self.entrypoint = entrypoint
        self.database = database
        self.work_db = work_db
        # only hypotheses that have a counterfactual can be made absent
        self.hypotheses = {h.id: h for h in hypotheses if h.testable}
        self.last_applied = ()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def edits_for(self, flags: Mapping[str, bool]) -> list:
        """One edit per candidate this phase requires to be ABSENT.

        A candidate marked present needs no edit: present is what the
        repository already says. That asymmetry is deliberate - the incident
        state is the repository untouched, so the phase that reproduces it
        applies nothing at all.
        """
        return [
            SourceEdit(file=h.file, before=h.observed, after=h.counterfactual,
                       label=h.id)
            for hypothesis_id, h in self.hypotheses.items()
            if not flags.get(hypothesis_id, True)
        ]

    def _compose(self, flags: Mapping[str, bool], extra_edits: Sequence) -> list:
        """The phase's own edits, plus any the caller layered on top.

        The fix protocol layers one: the repair, applied to the phases that
        run against the patched build. On a healthy CONTROL phase that repair
        is already there - the counterfactual a fix writes is the same text an
        ablation writes - so an extra edit whose `before` is already being
        replaced in the same file is dropped rather than applied twice. It is
        not a silent weakening: applying it would produce a byte-identical
        variant, and attempting it would fail, because the text it looks for
        is exactly the text the first edit removed.
        """
        edits = self.edits_for(flags)
        already = {(e.file, e.before) for e in edits}
        for extra in extra_edits:
            if (extra.file, extra.before) not in already:
                edits.append(extra)
                already.add((extra.file, extra.before))
        return edits

    def measure(self, fixture: dict, flags: Mapping[str, bool], reps: int,
                extra_edits: Sequence = ()) -> dict:
        variant = materialise(self.workspace, self.entrypoint,
                              self._compose(flags, extra_edits))
        self.last_applied = variant.applied
        try:
            sandbox = Sandbox(self.database, self.work_db,
                              service_path=variant.service_path).start()
            try:
                # no flags: this process IS the state
                return sandbox.measure(fixture, None, reps)
            finally:
                sandbox.stop()
        finally:
            variant.cleanup()

    def describe(self, flags: Mapping[str, bool], extra_edits: Sequence = ()) -> dict:
        edits = self._compose(flags, extra_edits)
        return {
            "kind": self.kind,
            "edits": [{"file": e.file, "from": e.before, "to": e.after,
                       "hypothesis": e.label} for e in edits],
            "unmodified": not edits,
        }
