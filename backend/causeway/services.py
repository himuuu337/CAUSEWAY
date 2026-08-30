"""Which repository a monitored service's incidents should be investigated
against - an explicit, human-made link, never guessed.

Causeway must never clone a repository nobody asked it to. A service can
report telemetry and accumulate risk indefinitely with no target
registered at all; an incident for it simply waits, visibly, for someone
to say where its code lives. Registration is in-memory only - a hackathon
MVP, and there is nothing here worth persisting past this process anyway,
since the repository URL is the only fact being remembered and the user
can always send it again.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, Optional

from causeway.intent.schema import DIAGNOSE_AND_FIX, MODES
from causeway.repository import RepositoryRejected, validate_url

_SERVICE_NAME_FIELD = "service"


@dataclass(frozen=True)
class ServiceTarget:
    service: str
    repository_url: str
    branch: str = ""
    investigation_mode: str = DIAGNOSE_AND_FIX

    def as_dict(self) -> dict:
        return {"service": self.service, "repository_url": self.repository_url,
               "branch": self.branch or None, "investigation_mode": self.investigation_mode}


class ServiceRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._targets: Dict[str, ServiceTarget] = {}

    def register(self, service: str, repository_url: str, branch: str = "",
                investigation_mode: str = DIAGNOSE_AND_FIX) -> ServiceTarget:
        if not isinstance(service, str) or not service.strip():
            raise ValueError("service must be a non-empty string")
        if investigation_mode not in MODES:
            raise ValueError("investigation_mode must be one of %s" % ", ".join(MODES))
        # The same allow-list validator every repository investigation
        # already goes through - registering a target is not a lighter-
        # weight way to point Causeway at something validate_url would
        # otherwise refuse.
        validate_url(repository_url)
        target = ServiceTarget(service=service, repository_url=repository_url,
                               branch=branch or "", investigation_mode=investigation_mode)
        with self._lock:
            self._targets[service] = target
        return target

    def get(self, service: str) -> Optional[ServiceTarget]:
        with self._lock:
            return self._targets.get(service)

    def all(self) -> Dict[str, ServiceTarget]:
        with self._lock:
            return dict(self._targets)

    def reset(self) -> None:
        with self._lock:
            self._targets.clear()


registry = ServiceRegistry()

__all__ = ["ServiceTarget", "ServiceRegistry", "registry", "RepositoryRejected"]
