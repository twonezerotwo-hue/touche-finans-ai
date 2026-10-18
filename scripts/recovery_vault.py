"""Encrypted, independent recovery snapshots.

The vault never stores its encryption key.  Callers provide a 32-byte key from
an external key-management mechanism (or ``TOUCHE_RECOVERY_VAULT_KEY`` in a
local controlled environment).  Restore is deliberately dry-run only: it
extracts into a new isolated directory and never overwrites a caller path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"TOUCHE-RECOVERY-VAULT\x01\n"
FORMAT_VERSION = 1


class VaultError(RuntimeError):
    """Raised when a vault cannot be trusted or safely handled."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _key(value: bytes | str | None) -> bytes:
    raw: bytes | str | None = value
    if raw is None:
        raw = os.environ.get("TOUCHE_RECOVERY_VAULT_KEY")
    if isinstance(raw, bytes):
        key = raw
    else:
        text = str(raw or "").strip()
        if not text:
            raise VaultError("Recovery vault anahtarı dış anahtar yönetiminden sağlanmalı.")
        try:
            key = bytes.fromhex(text) if len(text) == 64 else base64.urlsafe_b64decode(text + "=")
        except (ValueError, TypeError) as exc:
            raise VaultError("Recovery vault anahtarı 32 byte hex veya base64 olmalı.") from exc
    if len(key) != 32:
        raise VaultError("Recovery vault yalnız 32 byte AES-256 anahtar kabul eder.")
    return key


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _copy_sqlite_online(source: Path, target: Path) -> None:
    if not source.exists():
        raise VaultError(f"SQLite kaynağı bulunamadı: {source}")
    source_con = sqlite3.connect(source)
    target_con = sqlite3.connect(target)
    try:
        source_con.backup(target_con)
        target_con.commit()
    except sqlite3.Error as exc:
        raise VaultError(f"SQLite online backup başarısız: {exc}") from exc
    finally:
        target_con.close()
        source_con.close()


def _archive(staging: Path, manifest: dict[str, Any]) -> bytes:
    archive = staging / "payload.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for entry in manifest["files"]:
            path = staging / entry["path"]
            bundle.write(path, entry["path"])
        bundle.writestr("manifest.json", _json(manifest))
    return archive.read_bytes()


def create_snapshot(
    destination: Path,
    *,
    excel_path: Path,
    sqlite_path: Path,
    audit_path: Path | None = None,
    journal_path: Path | None = None,
    key: bytes | str | None = None,
    label: str = "synthetic",
) -> dict[str, Any]:
    """Create an encrypted snapshot without modifying any source file."""
    destination = Path(destination)
    if destination.exists():
        raise VaultError(f"Vault hedefi zaten var: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="touche-vault-") as temp:
        staging = Path(temp)
        files: list[dict[str, Any]] = []
        excel = staging / "excel.xlsx"
        if not Path(excel_path).exists():
            raise VaultError(f"Excel kaynağı bulunamadı: {excel_path}")
        shutil.copy2(excel_path, excel)
        db = staging / "finans.db"
        _copy_sqlite_online(Path(sqlite_path), db)
        for source, name in ((audit_path, "audit.jsonl"), (journal_path, "journal.jsonl")):
            if source is not None and Path(source).exists():
                shutil.copy2(source, staging / name)
        for path in sorted(staging.iterdir(), key=lambda item: item.name):
            if path.name == "payload.zip":
                continue
            files.append({"path": path.name, "sha256": _sha256(path), "size": path.stat().st_size})
        manifest = {
            "format": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": str(label),
            "files": files,
        }
        payload = _archive(staging, manifest)
    header = {
        "format": FORMAT_VERSION,
        "algorithm": "AES-256-GCM",
        "nonce": base64.urlsafe_b64encode(secrets.token_bytes(12)).decode("ascii"),
        "manifest_sha256": hashlib.sha256(_json(manifest)).hexdigest(),
    }
    header_bytes = _json(header)
    nonce_value = header.get("nonce")
    if not isinstance(nonce_value, str):
        raise TypeError("nonce missing")
    nonce = base64.urlsafe_b64decode(nonce_value)
    encrypted = AESGCM(_key(key)).encrypt(nonce, payload, header_bytes)
    encoded = MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + encrypted
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"vault": str(destination), "format": FORMAT_VERSION, "files": files, "encrypted": True}


def _decrypt(vault_path: Path, key: bytes | str | None) -> tuple[dict[str, Any], bytes]:
    raw = Path(vault_path).read_bytes()
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
        raise VaultError("Recovery vault başlığı bozuk.")
    offset = len(MAGIC)
    header_size = int.from_bytes(raw[offset:offset + 4], "big")
    offset += 4
    header_raw = raw[offset:offset + header_size]
    encrypted = raw[offset + header_size:]
    try:
        header = json.loads(header_raw.decode("utf-8"))
        nonce_value = header.get("nonce")
        if not isinstance(nonce_value, str):
            raise TypeError("nonce missing")
        nonce = base64.urlsafe_b64decode(nonce_value)
        payload = AESGCM(_key(key)).decrypt(nonce, encrypted, header_raw)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise VaultError("Recovery vault başlığı geçersiz.") from exc
    except Exception as exc:  # cryptography intentionally exposes no oracle details
        raise VaultError("Recovery vault doğrulanamadı veya anahtar yanlış.") from exc
    return header, payload


def restore_dry_run(vault_path: Path, destination: Path, *, key: bytes | str | None = None) -> dict[str, Any]:
    """Decrypt and verify into a new isolated directory; never overwrite files."""
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise VaultError("Dry-run restore hedefi boş veya mevcut değil.")
    header, payload = _decrypt(Path(vault_path), key)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="touche-restore-") as temp:
        archive = Path(temp) / "payload.zip"
        archive.write_bytes(payload)
        with zipfile.ZipFile(archive) as bundle:
            destination_root = destination.resolve()
            for member in bundle.infolist():
                member_path = (destination / member.filename).resolve()
                if not member_path.is_relative_to(destination_root):
                    raise VaultError("Snapshot yolu hedef dizinin dışına çıkıyor.")
            bundle.extractall(destination)
    manifest_path = destination / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if hashlib.sha256(_json(manifest)).hexdigest() != header["manifest_sha256"]:
            raise VaultError("Manifest hash doğrulanamadı.")
        for entry in manifest["files"]:
            path = destination / entry["path"]
            if not path.is_file() or _sha256(path) != entry["sha256"]:
                raise VaultError(f"Snapshot dosya hash'i uyuşmuyor: {entry['path']}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VaultError("Snapshot manifesti bozuk.") from exc
    return {"verified": True, "vault": str(vault_path), "destination": str(destination), "files": manifest["files"]}


def prune_snapshots(directory: Path, *, keep: int = 7) -> list[str]:
    """Remove only vault files beyond retention in the supplied vault directory."""
    if keep < 1:
        raise ValueError("En az bir recovery snapshot korunmalı.")
    paths = sorted(Path(directory).glob("*.vault"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for path in paths[keep:]:
        path.unlink()
        removed.append(str(path))
    return removed
