"""GitHub repository URL validation. No network, no filesystem - every case
here is a pure function of the URL string."""
from __future__ import annotations

import unittest

from causeway.repository.errors import RepositoryRejected
from causeway.repository.urlcheck import validate_url


class ValidURLTests(unittest.TestCase):
    def test_a_bare_owner_repo_url_is_accepted(self):
        ref = validate_url("https://github.com/foo/bar")
        self.assertEqual((ref.owner, ref.name), ("foo", "bar"))
        self.assertEqual(ref.url, "https://github.com/foo/bar")

    def test_a_trailing_dot_git_is_accepted_and_stripped(self):
        ref = validate_url("https://github.com/foo/bar.git")
        self.assertEqual((ref.owner, ref.name), ("foo", "bar"))

    def test_a_trailing_slash_is_accepted(self):
        ref = validate_url("https://github.com/foo/bar/")
        self.assertEqual((ref.owner, ref.name), ("foo", "bar"))

    def test_surrounding_whitespace_is_stripped(self):
        ref = validate_url("  https://github.com/foo/bar  ")
        self.assertEqual((ref.owner, ref.name), ("foo", "bar"))

    def test_hyphenated_and_dotted_names_are_accepted(self):
        ref = validate_url("https://github.com/my-org/my.repo_name-2")
        self.assertEqual((ref.owner, ref.name), ("my-org", "my.repo_name-2"))

    def test_the_same_ref_normalises_with_or_without_dot_git(self):
        a = validate_url("https://github.com/foo/bar")
        b = validate_url("https://github.com/foo/bar.git")
        self.assertEqual(a, b)


class InvalidURLTests(unittest.TestCase):
    def _rejects(self, url, stage=None):
        with self.assertRaises(RepositoryRejected) as caught:
            validate_url(url)
        if stage:
            self.assertEqual(caught.exception.stage, stage)
        return caught.exception

    def test_empty_string_is_rejected(self):
        self._rejects("")

    def test_none_is_rejected(self):
        self._rejects(None)

    def test_http_is_rejected(self):
        self._rejects("http://github.com/foo/bar")

    def test_a_non_github_host_is_rejected(self):
        self._rejects("https://evil.com/foo/bar")

    def test_a_lookalike_host_is_rejected(self):
        """github.com must match exactly - a suffix match would let
        'notgithub.com' or 'github.com.evil.com' through."""
        self._rejects("https://notgithub.com/foo/bar")
        self._rejects("https://github.com.evil.com/foo/bar")
        self._rejects("https://www.github.com/foo/bar")

    def test_file_scheme_is_rejected(self):
        self._rejects("file:///etc/passwd")

    def test_javascript_scheme_is_rejected(self):
        self._rejects("javascript:alert(1)")

    def test_localhost_is_rejected(self):
        self._rejects("https://localhost/foo/bar")

    def test_bare_host_with_no_path_is_rejected(self):
        self._rejects("https://github.com/")
        self._rejects("https://github.com")

    def test_owner_only_with_no_repo_is_rejected(self):
        self._rejects("https://github.com/foo")
        self._rejects("https://github.com/foo/")

    def test_credentials_in_the_url_are_rejected(self):
        self._rejects("https://user:pass@github.com/foo/bar")
        self._rejects("https://token@github.com/foo/bar")

    def test_a_port_is_rejected(self):
        self._rejects("https://github.com:8443/foo/bar")

    def test_path_traversal_is_rejected(self):
        self._rejects("https://github.com/foo/../../etc/passwd")
        self._rejects("https://github.com/foo/bar/../baz")

    def test_extra_path_segments_are_rejected(self):
        self._rejects("https://github.com/foo/bar/tree/main")
        self._rejects("https://github.com/foo/bar/issues/1")

    def test_a_repo_name_of_dot_or_dotdot_is_rejected(self):
        self._rejects("https://github.com/foo/.")
        self._rejects("https://github.com/foo/..")

    def test_percent_encoded_slashes_cannot_smuggle_extra_segments(self):
        self._rejects("https://github.com/foo/bar%2F..%2Fbaz")

    def test_a_non_string_is_rejected(self):
        self._rejects(12345)
        self._rejects(["https://github.com/foo/bar"])


if __name__ == "__main__":
    unittest.main()
