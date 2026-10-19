"""New operational-closure evidence; all data is synthetic and temporary."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import finans_cekirdegi as cekirdek
import pytest
import recovery_offsite
import recovery_vault
from yardimci_excel import tam_akilli_excel


def _old_db(path: Path, *, duplicate: bool = False) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE finans_islemleri(
          transaction_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
          source_hash TEXT NOT NULL, financial_hash TEXT NOT NULL,
          approval_identity TEXT NOT NULL, firma_key TEXT NOT NULL,
          belge_turu TEXT NOT NULL, state TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          expected_excel_ids TEXT NOT NULL, expected_excel_fingerprint TEXT,
          expected_db_ids TEXT NOT NULL, workbook_hash_before TEXT,
          workbook_hash_after TEXT, recovery_attempts INTEGER NOT NULL,
          last_error TEXT, audit_repair_required INTEGER NOT NULL
        );
        """
    )
    row = ("tx-old", "doc-old", "source", "financial", "owner", "gursar_mimarlik", "dekont", "COMMITTED", "2026-10-19", "2026-10-19", "[]", None, "[]", None, None, 0, None, 0)
    con.execute("INSERT INTO finans_islemleri VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
    if duplicate:
        con.execute("INSERT INTO finans_islemleri VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("tx-old-2", "doc-old", *row[2:]))
    con.commit()
    con.close()


def test_additive_migration_preserves_synthetic_financial_rows(tmp_path: Path):
    db = tmp_path / "old.db"
    _old_db(db)
    with patch.object(cekirdek, "DB_PATH", db):
        cekirdek.veritabanini_hazirla()
        cekirdek.veritabanini_hazirla()
    con = sqlite3.connect(db)
    row = con.execute("SELECT document_id,state,expected_db_fingerprint FROM finans_islemleri").fetchone()
    version = con.execute("SELECT deger FROM schema_meta WHERE anahtar='surum'").fetchone()[0]
    con.close()
    assert row == ("doc-old", "COMMITTED", None)
    assert version == "4"


def test_failed_migration_is_fail_closed_without_deleting_rows(tmp_path: Path):
    db = tmp_path / "duplicate.db"
    _old_db(db, duplicate=True)
    with patch.object(cekirdek, "DB_PATH", db), pytest.raises(sqlite3.IntegrityError):
        cekirdek.veritabanini_hazirla()
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM finans_islemleri").fetchone()[0] == 2
    con.close()


def test_offsite_emulator_upload_download_checksum_retention_and_fail_closed(tmp_path: Path):
    source = tmp_path / "release.vault"
    source.write_bytes(b"TOUCHE-RECOVERY-VAULT synthetic encrypted bytes")
    store = recovery_offsite.LocalOffsiteStore(tmp_path / "offsite")
    record = store.put(source, version="r1")
    assert record["encrypted"] is True
    assert store.verify("r1")["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    destination = tmp_path / "downloaded.vault"
    store.get("r1", destination)
    assert destination.read_bytes() == source.read_bytes()
    existing = tmp_path / "existing.vault"
    existing.write_bytes(b"do-not-overwrite")
    with pytest.raises(recovery_offsite.OffsiteStoreError):
        store.get("r1", existing)
    (tmp_path / "offsite" / "r1.vault").write_bytes(b"tampered")
    with pytest.raises(recovery_offsite.OffsiteStoreError):
        store.verify("r1")


def test_offsite_retention_keeps_only_newest_versions(tmp_path: Path):
    source = tmp_path / "release.vault"
    source.write_bytes(b"synthetic encrypted payload")
    store = recovery_offsite.LocalOffsiteStore(tmp_path / "offsite")
    for version in ("r1", "r2", "r3"):
        store.put(source, version=version)
        time.sleep(0.01)
    removed = store.prune(keep=1)
    assert len(removed) == 2
    assert store.list_versions() == ["r3"]


def test_onedrive_like_external_change_is_rejected_by_writer(tmp_path: Path):
    main = tam_akilli_excel(tmp_path / "main.xlsx")
    backup = tmp_path / "backup.xlsx"
    import excel_deposu as depo
    from _ortam import AnaExcelYonlendirme

    with AnaExcelYonlendirme(main, backup):
        def change_workbook(wb):
            wb["99_Ayarlar"]["A30"] = "external-change"
            wb.save(main)
            return {"basari": True, "yazilan": 1}

        # Exercise the guarded writer directly; no production path is involved.
        with pytest.raises(depo.DepoHatasi, match="dışarıdan değişti"):
            depo.guvenli_degistir(change_workbook, beklenen_sayfalar=("99_Ayarlar",), kaynak="synthetic")


@pytest.mark.parametrize("count", [10, 100, 500])
def test_bounded_synthetic_performance_snapshot_restore(tmp_path: Path, count: int, capsys):
    excel = tam_akilli_excel(tmp_path / f"{count}.xlsx")
    db = tmp_path / f"{count}.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tx(id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)")
    con.executemany("INSERT INTO tx(amount) VALUES(?)", [(i + 1,) for i in range(count)])
    con.commit()
    con.close()
    key = b"k" * 32
    vault = tmp_path / f"{count}.vault"
    started = time.perf_counter()
    recovery_vault.create_snapshot(vault, excel_path=excel, sqlite_path=db, key=key, label=f"perf-{count}")
    snapshot_seconds = time.perf_counter() - started
    restore = tmp_path / f"restore-{count}"
    started = time.perf_counter()
    recovery_vault.restore_dry_run(vault, restore, key=key)
    restore_seconds = time.perf_counter() - started
    assert snapshot_seconds < 10 and restore_seconds < 10
    print(f"synthetic_count={count} snapshot_seconds={snapshot_seconds:.4f} restore_seconds={restore_seconds:.4f}")
