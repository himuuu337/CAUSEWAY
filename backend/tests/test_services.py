"""causeway.services: the explicit, in-memory service-to-repository link -
never invented, never a stand-in for cloning something nobody registered.
"""
from __future__ import annotations

import unittest

from causeway.repository import RepositoryRejected
from causeway.services import ServiceRegistry


class RegistrationTests(unittest.TestCase):
    def test_a_well_formed_registration_is_accepted(self):
        registry = ServiceRegistry()
        target = registry.register("order-service", "https://github.com/owner/repo")
        self.assertEqual(target.service, "order-service")
        self.assertEqual(target.repository_url, "https://github.com/owner/repo")
        self.assertEqual(registry.get("order-service"), target)

    def test_an_unregistered_service_is_none(self):
        registry = ServiceRegistry()
        self.assertIsNone(registry.get("nobody"))

    def test_registering_again_replaces_the_previous_target(self):
        registry = ServiceRegistry()
        registry.register("s", "https://github.com/a/b")
        registry.register("s", "https://github.com/c/d")
        self.assertEqual(registry.get("s").repository_url, "https://github.com/c/d")

    def test_an_empty_service_name_is_rejected(self):
        registry = ServiceRegistry()
        with self.assertRaises(ValueError):
            registry.register("", "https://github.com/a/b")

    def test_an_invalid_mode_is_rejected(self):
        registry = ServiceRegistry()
        with self.assertRaises(ValueError):
            registry.register("s", "https://github.com/a/b", investigation_mode="not_a_mode")

    def test_the_same_url_validator_every_investigation_uses_is_reused(self):
        """Registering a target is not a lighter-weight way to point
        Causeway at something the real investigation path would refuse."""
        registry = ServiceRegistry()
        with self.assertRaises(RepositoryRejected):
            registry.register("s", "http://not-github.example/a/b")
        with self.assertRaises(RepositoryRejected):
            registry.register("s", "https://github.com/a/b/../../etc")

    def test_all_returns_every_registered_target(self):
        registry = ServiceRegistry()
        registry.register("a", "https://github.com/o/a")
        registry.register("b", "https://github.com/o/b")
        self.assertEqual(set(registry.all()), {"a", "b"})

    def test_reset_clears_every_registration(self):
        registry = ServiceRegistry()
        registry.register("a", "https://github.com/o/a")
        registry.reset()
        self.assertEqual(registry.all(), {})


if __name__ == "__main__":
    unittest.main()
