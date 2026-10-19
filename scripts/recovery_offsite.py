"""Fail-closed storage adapter for encrypted recovery snapshots.

The adapter deliberately accepts only already-encrypted ``.vault`` files.  The
default implementation is a local directory emulator for acceptance tests and
controlled operations; it does not contain cloud credentials or provider SDKs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OffsiteStoreError(RuntimeError):
    """Raised when an offsite snapshot cannot be verified safely."""


_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalOffsiteStore:
    """Encrypted-snapshot store backed by an isolated directory.

    This is intentionally provider-neutral.  A future cloud implementation can
    satisfy the same operations, while tests use this emulator without any
    network or real credentials.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check_version(version: str) -> str:
        value = str(version or "").strip()
        if not _VERSION.fullmatch(value):
            raise OffsiteStoreError("Snapshot sürümü geçersiz.")
        return value

    def _paths(self, version: str) -> tuple[Path, Path]:
        value = self._check_version(version)
        return self.root / f"{value}.vault", self.root / f"{value}.json"

    def put(self, snapshot: Path, *, version: str) -> dict[str, Any]:
        """Upload an encrypted vault and verify the stored bytes."""
        source = Path(snapshot)
        if source.suffix.lower() != ".vault" or not source.is_file():
            raise OffsiteStoreError("Yalnız doğrulanmış .vault dosyası yüklenebilir.")
        target, metadata = self._paths(version)
        if target.exists() or metadata.exists():
            raise OffsiteStoreError("Aynı snapshot sürümü zaten mevcut.")
        digest = _sha256(source)
        record = {
            "version": self._check_version(version),
            "sha256": digest,
            "size": source.stat().st_size,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "encrypted": True,
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{version}.", suffix=".tmp", dir=self.root)
        os.close(descriptor)
        temporary = Path(temporary_name)
        metadata_tmp = metadata.with_name(f".{metadata.name}.{os.getpid()}.tmp")
        target_placed = False
        try:
            shutil.copyfile(source, temporary)
            if _sha256(temporary) != digest:
                raise OffsiteStoreError("Yükleme doğrulaması başarısız.")
            os.replace(temporary, target)
            target_placed = True
            metadata_tmp.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(metadata_tmp, metadata)
            if _sha256(target) != digest:
                target.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)
                raise OffsiteStoreError("Depolanan snapshot hash'i uyuşmuyor.")
        except Exception:
            if target_placed:
                target.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            metadata_tmp.unlink(missing_ok=True)
        return record

    def verify(self, version: str) -> dict[str, Any]:
        """Verify metadata and encrypted bytes without modifying anything."""
        target, metadata = self._paths(version)
        if not target.is_file() or not metadata.is_file():
            raise OffsiteStoreError("Snapshot veya manifest eksik.")
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise OffsiteStoreError("Snapshot manifesti bozuk.") from exc
        if record.get("version") != self._check_version(version) or not record.get("encrypted"):
            raise OffsiteStoreError("Snapshot manifesti güvenilir değil.")
        actual = _sha256(target)
        if actual != record.get("sha256") or target.stat().st_size != record.get("size"):
            raise OffsiteStoreError("Snapshot checksum doğrulaması başarısız.")
        return dict(record)

    def get(self, version: str, destination: Path) -> dict[str, Any]:
        """Download a verified snapshot without overwriting a destination."""
        source, _ = self._paths(version)
        record = self.verify(version)
        destination = Path(destination)
        if destination.exists():
            raise OffsiteStoreError("İndirme hedefi zaten mevcut; üzerine yazılmadı.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            if _sha256(temporary) != record["sha256"]:
                raise OffsiteStoreError("İndirme checksum doğrulaması başarısız.")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return record

    def list_versions(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.vault"))

    def prune(self, *, keep: int = 7) -> list[str]:
        if keep < 1:
            raise ValueError("En az bir snapshot korunmalı.")
        versions = sorted(self.list_versions(), key=lambda item: self._paths(item)[0].stat().st_mtime, reverse=True)
        removed: list[str] = []
        for version in versions[keep:]:
            vault, metadata = self._paths(version)
            vault.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            removed.append(version)
        return removed
