"""PR-14 operational resilience contracts; all paths are disposable."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import recovery_operations as ops
from recovery_offsite import LocalOffsiteStore


def test_key_ring_generates_32_byte_keys_rotates_and_never_exposes_secret():
    ring = ops.KeyRing()
    first = ring.generate()
    first_key = ring.key(first.key_id)
    second = ring.generate()
    assert len(first_key) == 32
    assert ring.active_id() == second.key_id
    assert ring.descriptor(first.key_id).status == "retired"
    assert first_key.hex() not in repr(first)
    with pytest.raises(ops.RecoveryOperationError, match="Aktif"):
        ring.revoke(second.key_id)
    assert ring.revoke(first.key_id).status == "revoked"
    with pytest.raises(ops.RecoveryOperationError, match="bulunamadı"):
        ring.key(first.key_id)


def test_upload_retry_is_bounded_and_does_not_retry_after_success(tmp_path: Path):
    class Flaky:
        def __init__(self):
            self.calls = 0

        def put(self, snapshot: Path, *, version: str):
            self.calls += 1
            if self.calls < 3:
                raise OSError("synthetic transient")
            return {"version": version, "encrypted": True}

        def verify(self, version: str):
            return {"version": version}

    source = tmp_path / "snapshot.vault"
    source.write_bytes(b"encrypted synthetic")
    delays: list[float] = []
    flaky = Flaky()
    result = ops.upload_with_retry(flaky, source, version="r1", sleep=delays.append)
    assert result["attempts"] == 3
    assert flaky.calls == 3
    assert delays == [0.05, 0.1]


def test_upload_retry_fails_closed_after_max_attempts(tmp_path: Path):
    class Broken:
        def put(self, snapshot: Path, *, version: str):
            raise OSError("offline")

        def verify(self, version: str):
            raise AssertionError("verify should not run")

    source = tmp_path / "snapshot.vault"
    source.write_bytes(b"synthetic")
    with pytest.raises(ops.RecoveryOperationError, match="3 denemede"):
        ops.upload_with_retry(Broken(), source, version="r1", sleep=lambda _: None)


def test_verify_versions_reports_corruption_without_modifying_store(tmp_path: Path):
    source = tmp_path / "snapshot.vault"
    source.write_bytes(b"ciphertext")
    store = LocalOffsiteStore(tmp_path / "offsite")
    store.put(source, version="r1")
    (tmp_path / "offsite" / "r1.vault").write_bytes(b"tampered")
    report = ops.verify_versions(store, ["r1", "missing"])
    assert report["status"] == "degraded"
    assert {x["version"] for x in report["failures"]} == {"r1", "missing"}


def test_backup_health_reports_stale_and_recovery_backlog_without_financial_data():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    old = "2026-10-17T00:00:00+00:00"
    report = ops.backup_health(
        last_success_at=old,
        last_verified_at=None,
        recovery={"pending_recovery": 1, "needs_review": 2, "audit_repair_required": 1},
        verification={"status": "degraded", "failures": [{"version": "r1"}]},
        now=now,
        max_age_seconds=3600,
    )
    assert report["status"] == "degraded"
    assert set(report["alerts"]) == {
        "BACKUP_STALE",
        "VERIFICATION_MISSING",
        "RECOVERY_BACKLOG",
        "AUDIT_REPAIR_BACKLOG",
        "SNAPSHOT_VERIFICATION_FAILED",
    }
    assert "amount" not in str(report).lower()


def test_backup_health_is_healthy_only_when_verified_and_current():
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    stamp = "2026-10-19T00:00:00+00:00"
    report = ops.backup_health(
        last_success_at=stamp,
        last_verified_at=stamp,
        recovery={},
        verification={"status": "ok", "failures": []},
        now=now,
    )
    assert report["status"] == "healthy"
    assert report["alerts"] == []


def test_atomic_sync_copy_detects_external_change_and_preserves_target(tmp_path: Path):
    source = tmp_path / "source.vault"
    target = tmp_path / "target.vault"
    source.write_bytes(b"new encrypted snapshot")
    target.write_bytes(b"old snapshot")
    baseline = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_bytes(b"external change")
    with pytest.raises(ops.RecoveryOperationError, match="çakışma"):
        ops.atomic_sync_copy(source, target, expected_target_hash=baseline)
    assert target.read_bytes() == b"external change"


def test_atomic_sync_copy_is_atomic_and_cleans_temp_files(tmp_path: Path):
    source = tmp_path / "source.vault"
    target = tmp_path / "nested" / "target.vault"
    source.write_bytes(b"new encrypted snapshot")
    digest = ops.atomic_sync_copy(source, target)
    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert target.read_bytes() == source.read_bytes()
    assert list(target.parent.glob(".*.tmp")) == []


def test_offsite_upload_retry_and_version_verification_are_provider_neutral(tmp_path: Path):
    source = tmp_path / "snapshot.vault"
    source.write_bytes(b"encrypted snapshot bytes")
    store = LocalOffsiteStore(tmp_path / "remote")
    result = ops.upload_with_retry(store, source, version="2026-10-19-01", sleep=lambda _: None)
    assert result["record"]["encrypted"] is True
    report = ops.verify_versions(store, ["2026-10-19-01"])
    assert report["status"] == "ok"
    assert report["checked"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
