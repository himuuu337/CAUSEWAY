"""The one error shape repository ingestion raises, at any stage.

A single exception type rather than one per failure keeps the orchestrator's
catch simple, while `stage` still says which lifecycle step rejected the
repository (url, clone, manifest) and `reason` carries the one line a human
reads. Nothing about accepting a repository is ever partial: any raise here
means the repository does not proceed to execution.
"""
from __future__ import annotations


class RepositoryRejected(Exception):
    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
