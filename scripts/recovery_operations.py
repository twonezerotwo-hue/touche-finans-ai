"""Operational recovery helpers with provider-neutral, fail-closed semantics.

The module deliberately contains no cloud SDK, credential lookup, or financial
path discovery.  It is suitable for disposable acceptance environments and for
an eventual provider adapter whose security contract can be tested here.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class SnapshotStore(Protocol):
    def put(self, snapshot: Path, *, version: str) -> dict[str, Any]: ...

    def verify(self, version: str) -> dict[str, Any]: ...


class RecoveryOperationError(RuntimeError):
    """Raised when an operation cannot be proven safe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class KeyDescriptor:
    """Non-secret key metadata; the key bytes are never included."""

    key_id: str
    created_at: str
    status: str
    purpose: str


class KeyRing:
    """In-memory key registry for synthetic drills.

    Production key custody must be supplied by a KMS/secret manager.  This
    class intentionally cannot persist or print key material.
    """

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}
        self._descriptors: dict[str, KeyDescriptor] = {}
        self._active: str | None = None

    def generate(self, *, purpose: str = "recovery-vault") -> KeyDescriptor:
        key_id = f"rk_{secrets.token_hex(12)}"
        self._keys[key_id] = secrets.token_bytes(32)
        descriptor = KeyDescriptor(
            key_id=key_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="active",
            purpose=purpose,
        )
        self._descriptors[key_id] = descriptor
        if self._active is not None:
            old = self._descriptors[self._active]
            self._descriptors[self._active] = KeyDescriptor(
                old.key_id, old.created_at, "retired", old.purpose
            )
        self._active = key_id
        return descriptor

    def active_id(self) -> str:
        if self._active is None:
            raise RecoveryOperationError("Aktif recovery anahtarı yok.")
        return self._active

    def descriptor(self, key_id: str) -> KeyDescriptor:
        try:
            return self._descriptors[key_id]
        except KeyError as exc:
            raise RecoveryOperationError("Recovery anahtar kimliği bulunamadı.") from exc

    def key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError as exc:
            raise RecoveryOperationError("Recovery anahtarı bulunamadı.") from exc

    def revoke(self, key_id: str) -> KeyDescriptor:
        descriptor = self.descriptor(key_id)
        if key_id == self._active:
            raise RecoveryOperationError("Aktif anahtar önce rotate edilmelidir.")
        self._keys.pop(key_id, None)
        revoked = KeyDescriptor(descriptor.key_id, descriptor.created_at, "revoked", descriptor.purpose)
        self._descriptors[key_id] = revoked
        return revoked

    def descriptors(self) -> tuple[KeyDescriptor, ...]:
        return tuple(self._descriptors.values())


def upload_with_retry(
    store: SnapshotStore,
    snapshot: Path,
    *,
    version: str,
    attempts: int = 3,
    base_delay: float = 0.05,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Upload an already encrypted snapshot with bounded exponential retry."""
    if attempts < 1 or attempts > 5:
        raise ValueError("attempts 1 ile 5 arasında olmalıdır.")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            record = store.put(Path(snapshot), version=version)
            return {"record": record, "attempts": attempt}
        except Exception as exc:  # provider errors are intentionally normalized at boundary
            last_error = exc
            if attempt < attempts:
                sleep(base_delay * (2 ** (attempt - 1)))
    raise RecoveryOperationError(f"Snapshot yükleme {attempts} denemede başarısız.") from last_error


def verify_versions(store: SnapshotStore, versions: list[str]) -> dict[str, Any]:
    """Verify each version without changing the store; expose only safe metadata."""
    checked: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for version in versions:
        try:
            record = store.verify(version)
            checked.append({"version": version, "verified": True, "sha256": record.get("sha256")})
        except Exception as exc:  # noqa: BLE001 - health reporting must continue
            failures.append({"version": version, "error": type(exc).__name__})
    return {"checked": checked, "failures": failures, "status": "ok" if not failures else "degraded"}


def backup_health(
    *,
    last_success_at: str | None,
    last_verified_at: str | None,
    recovery: dict[str, Any] | None = None,
    verification: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_age_seconds: int = 86400,
) -> dict[str, Any]:
    """Return an operational summary containing no financial row data."""
    current = now or datetime.now(timezone.utc)
    alerts: list[str] = []

    def age(value: str | None) -> int | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int((current - parsed).total_seconds()))
        except ValueError:
            return None

    success_age = age(last_success_at)
    verified_age = age(last_verified_at)
    if success_age is None:
        alerts.append("BACKUP_MISSING")
    elif success_age > max_age_seconds:
        alerts.append("BACKUP_STALE")
    if verified_age is None:
        alerts.append("VERIFICATION_MISSING")
    elif verified_age > max_age_seconds:
        alerts.append("VERIFICATION_STALE")
    recovery = recovery or {}
    if int(recovery.get("pending_recovery", 0) or 0) or int(recovery.get("needs_review", 0) or 0):
        alerts.append("RECOVERY_BACKLOG")
    if int(recovery.get("audit_repair_required", 0) or 0):
        alerts.append("AUDIT_REPAIR_BACKLOG")
    if verification and verification.get("failures"):
        alerts.append("SNAPSHOT_VERIFICATION_FAILED")
    return {
        "status": "healthy" if not alerts else "degraded",
        "alerts": alerts,
        "last_success_at": last_success_at,
        "last_verified_at": last_verified_at,
        "success_age_seconds": success_age,
        "verified_age_seconds": verified_age,
        "recovery": {
            "pending_recovery": int(recovery.get("pending_recovery", 0) or 0),
            "needs_review": int(recovery.get("needs_review", 0) or 0),
            "audit_repair_required": int(recovery.get("audit_repair_required", 0) or 0),
        },
        "verification_status": (verification or {}).get("status", "not_run"),
    }


def atomic_sync_copy(source: Path, target: Path, *, expected_target_hash: str | None = None) -> str:
    """Copy a synthetic sync artifact only when the destination is unchanged."""
    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise RecoveryOperationError("Kaynak snapshot bulunamadı.")
    if expected_target_hash is not None:
        if not target.is_file() or _sha256(target) != expected_target_hash:
            raise RecoveryOperationError("Dış değişiklik/çakışma algılandı; yazma yapılmadı.")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        digest = _sha256(temporary)
        os.replace(temporary, target)
        if _sha256(target) != digest:
            raise RecoveryOperationError("Atomic kopya doğrulaması başarısız.")
        return digest
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "KeyDescriptor",
    "KeyRing",
    "RecoveryOperationError",
    "atomic_sync_copy",
    "backup_health",
    "upload_with_retry",
    "verify_versions",
]
