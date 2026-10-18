"""PR-04 encrypted recovery vault tests; every source is synthetic."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest
import recovery_vault


class TestRecoveryVault:
    def setup_method(self):
        self.root = Path(tempfile.mkdtemp(prefix="touche-vault-tests-"))
        self.key = bytes(range(32))
        self.excel = self.root / "main.xlsx"
        self.excel.write_bytes(b"synthetic workbook bytes")
        self.db = self.root / "finans.db"
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount INTEGER)")
            con.execute("INSERT INTO ledger VALUES (1, 12345)")
        self.audit = self.root / "audit.jsonl"
        self.audit.write_text('{"event":"synthetic"}\n', encoding="utf-8")

    def teardown_method(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_roundtrip_uses_online_sqlite_backup_and_verifies_manifest(self):
        vault = self.root / "snapshot.vault"
        result = recovery_vault.create_snapshot(
            vault, excel_path=self.excel, sqlite_path=self.db,
            audit_path=self.audit, key=self.key, label="test",
        )
        assert result["encrypted"] is True
        restored = self.root / "restore"
        report = recovery_vault.restore_dry_run(vault, restored, key=self.key)
        assert report["verified"] is True
        assert (restored / "excel.xlsx").read_bytes() == self.excel.read_bytes()
        con = sqlite3.connect(restored / "finans.db")
        try:
            assert con.execute("SELECT amount FROM ledger").fetchone()[0] == 12345
        finally:
            con.close()
        assert (restored / "audit.jsonl").read_text(encoding="utf-8") == self.audit.read_text(encoding="utf-8")

    def test_wrong_key_and_corruption_fail_closed(self):
        vault = self.root / "snapshot.vault"
        recovery_vault.create_snapshot(vault, excel_path=self.excel, sqlite_path=self.db, key=self.key)
        with pytest.raises(recovery_vault.VaultError, match="doğrulanamadı"):
            recovery_vault.restore_dry_run(vault, self.root / "wrong", key=b"x" * 32)
        data = bytearray(vault.read_bytes())
        data[-1] ^= 0xFF
        vault.write_bytes(data)
        with pytest.raises(recovery_vault.VaultError, match="doğrulanamadı"):
            recovery_vault.restore_dry_run(vault, self.root / "corrupt", key=self.key)

    def test_restore_never_overwrites_nonempty_destination(self):
        vault = self.root / "snapshot.vault"
        recovery_vault.create_snapshot(vault, excel_path=self.excel, sqlite_path=self.db, key=self.key)
        target = self.root / "existing"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with pytest.raises(recovery_vault.VaultError, match="boş"):
            recovery_vault.restore_dry_run(vault, target, key=self.key)
        assert marker.read_text(encoding="utf-8") == "keep"

    def test_key_is_external_and_retention_only_removes_vault_files(self, monkeypatch):
        monkeypatch.setenv("TOUCHE_RECOVERY_VAULT_KEY", self.key.hex())
        for index in range(3):
            recovery_vault.create_snapshot(
                self.root / f"{index}.vault", excel_path=self.excel, sqlite_path=self.db, label=str(index)
            )
        keep = self.root / "do-not-delete.txt"
        keep.write_text("safe", encoding="utf-8")
        removed = recovery_vault.prune_snapshots(self.root, keep=1)
        assert len(removed) == 2
        assert keep.exists()
        assert len(list(self.root.glob("*.vault"))) == 1
