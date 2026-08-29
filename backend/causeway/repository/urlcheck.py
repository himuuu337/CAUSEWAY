"""Strict validation of a pasted GitHub repository URL.

MVP contract: exactly https://github.com/<owner>/<repo>, an optional
trailing .git, and nothing else - no other host, no credentials, no port, no
extra path segments, no scheme but https. Every check here is an allow-list
against the shape GitHub itself uses for owner and repository names, not a
denylist of things to reject, so nothing gets through by not being on a list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from causeway.repository.errors import RepositoryRejected

# GitHub owner (user/org) names: alphanumeric or single hyphens, 1-39 chars,
# never starting or ending with a hyphen.
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
# GitHub repository names: alphanumeric, dot, underscore, hyphen, up to 100
# chars. Lazy so a trailing `.git` is not swallowed into the captured name.
_REPO = r"[A-Za-z0-9._-]{1,100}?"
_PATH_RE = re.compile(r"^/(?P<owner>%s)/(?P<repo>%s)(?:\.git)?/?$" % (_OWNER, _REPO))


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str
    url: str   # canonical https://github.com/<owner>/<repo> - no .git, no trailing slash


def validate_url(raw: str) -> RepoRef:
    """Parse and validate a GitHub repository URL, or raise RepositoryRejected."""
    if not isinstance(raw, str) or not raw.strip():
        raise RepositoryRejected("url", "no repository URL was given")
    text = raw.strip()

    try:
        parsed = urlsplit(text)
    except ValueError:
        raise RepositoryRejected("url", "could not parse %r as a URL" % text)

    if parsed.scheme.lower() != "https":
        raise RepositoryRejected(
            "url", "only https:// GitHub URLs are supported, not %r"
            % (parsed.scheme or "(none)"))
    if parsed.username or parsed.password:
        raise RepositoryRejected("url", "the URL must not contain credentials")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise RepositoryRejected("url", "could not parse the URL's host")
    if host is None or host.lower() != "github.com":
        raise RepositoryRejected(
            "url", "only github.com repositories are supported, not %r"
            % (host or parsed.netloc))
    if port is not None:
        raise RepositoryRejected("url", "the URL must not specify a port")

    match = _PATH_RE.match(parsed.path)
    if not match:
        raise RepositoryRejected(
            "url", "expected https://github.com/<owner>/<repo>, got path %r"
            % parsed.path)

    owner, repo = match.group("owner"), match.group("repo")
    if repo in (".", ".."):
        raise RepositoryRejected("url", "%r is not a valid repository name" % repo)

    return RepoRef(owner=owner, name=repo, url="https://github.com/%s/%s" % (owner, repo))
