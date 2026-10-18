"""PR-05 synthetic trust-boundary tests; no live network or finance files."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import ui_yetki
import yetki


class SecurityBoundaryTests(unittest.TestCase):
    def test_untrusted_client_cannot_spoof_tailscale_identity_header(self):
        session = ui_yetki.oturum(
            {"Tailscale-User-Login": "owner@example.test", "Tailscale-User-Name": "Owner"},
            "finance.example.test",
            "10.20.30.40",
        )
        self.assertFalse(session["yetkili"])
        self.assertEqual(session["kimlik"], "")
        self.assertFalse(session["yerel"])

    def test_explicit_trusted_proxy_can_forward_identity(self):
        with patch.dict(os.environ, {"TOUCHE_TRUSTED_PROXY_IPS": "10.20.30.40/32"}, clear=False):
            session = ui_yetki.oturum(
                {"Tailscale-User-Login": "owner@example.test"},
                "finance.example.test",
                "10.20.30.40",
            )
        self.assertEqual(session["kimlik"], "owner@example.test")
        self.assertFalse(session["yerel"])

    def test_local_admin_requires_allowlisted_host(self):
        denied = ui_yetki.oturum({}, "attacker.example.test", "127.0.0.1")
        self.assertFalse(denied["yetkili"])
        allowed = ui_yetki.oturum({}, "localhost:8765", "127.0.0.1")
        self.assertTrue(allowed["yetkili"])
        self.assertEqual(allowed["rol"], "OWNER")

    def test_recovery_permission_is_cfo_or_owner_only(self):
        self.assertFalse(yetki.izin_var("FINANCE_MANAGER", "recovery.authorize"))
        self.assertFalse(yetki.izin_var("ACCOUNTANT", "recovery.authorize"))
        self.assertTrue(yetki.izin_var("CFO", "recovery.authorize"))
        self.assertTrue(yetki.izin_var("OWNER", "recovery.authorize"))


if __name__ == "__main__":
    unittest.main()
