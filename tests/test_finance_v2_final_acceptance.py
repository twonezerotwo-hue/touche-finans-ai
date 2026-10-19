"""PR-15 final acceptance contracts on disposable synthetic environments."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import cfo_intelligence as intelligence
import finans_cekirdegi as cekirdek
import finans_provenance as provenance
import recovery_operations as operations
import recovery_vault
from cfo_test_temeli import BUGUN, CfoTestTemeli
from recovery_offsite import LocalOffsiteStore


class FinalAcceptance(CfoTestTemeli):
    """One full synthetic workflow plus corpus, safety and performance evidence."""

    def test_full_financial_workflow_to_provenance_backup_restore_and_reconcile(self):
        started = time.perf_counter()
        draft = self.baslat("Gürsar Mimarlık Göktürk Okulu Devrim'e 17 bin TL ödeme yapıldı")
        draft = self.sec(draft, "hesap", "Gürsar TL Banka")
        assert draft["kayit_taslagi"]["durum"] == "ONAY_BEKLIYOR"
        assert self.ledger() == []
        approved = self.onayla(draft)
        posting_seconds = time.perf_counter() - started
        assert approved["tamam"] is True
        assert len(self.ledger()) == 1

        with cekirdek._baglanti() as con:
            fisler, _ = __import__("proje_finansi")._satirlari_oku(con, None)
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        evidence = provenance.metric_provenance(
            "payments", fisler, faaliyet="gursar_mimarlik", proje="Göktürk Okulu"
        )
        assert evidence["reconciliation_status"] == "CONSISTENT"
        assert evidence["source_count"] == 1

        key = b"z" * 32
        vault = self.root / "vault" / "final.vault"
        snapshot_started = time.perf_counter()
        recovery_vault.create_snapshot(
            vault,
            excel_path=self.ana,
            sqlite_path=self.root / "finans.db",
            audit_path=self.audit,
            key=key,
            label="pr15-final-synthetic",
        )
        snapshot_seconds = time.perf_counter() - snapshot_started
        store = LocalOffsiteStore(self.root / "offsite")
        uploaded = operations.upload_with_retry(store, vault, version="pr15-01", sleep=lambda _: None)
        assert uploaded["record"]["encrypted"] is True
        assert operations.verify_versions(store, ["pr15-01"])["status"] == "ok"
        restore_started = time.perf_counter()
        restored = self.root / "restored"
        result = recovery_vault.restore_dry_run(vault, restored, key=key)
        restore_seconds = time.perf_counter() - restore_started
        assert result["verified"] is True
        assert (restored / "excel.xlsx").exists() and (restored / "finans.db").exists()
        restored_con = sqlite3.connect(restored / "finans.db")
        try:
            assert restored_con.execute("SELECT COUNT(*) FROM fisler").fetchone()[0] == 1
        finally:
            restored_con.close()
        health = operations.backup_health(
            last_success_at=datetime.now(timezone.utc).isoformat(),
            last_verified_at=datetime.now(timezone.utc).isoformat(),
            recovery={},
            verification={"status": "ok", "failures": []},
        )
        assert health["status"] == "healthy"
        print(
            f"PR15_SYNTHETIC transaction=1 posting_seconds={posting_seconds:.4f} "
            f"snapshot_seconds={snapshot_seconds:.4f} restore_seconds={restore_seconds:.4f} rpo_lost=0"
        )

    def test_turkish_corpus_is_deterministic_and_queries_never_become_drafts(self):
        corpus_path = Path(__file__).parent / "fixtures" / "cfo_turkish_commands.v1.json"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        before = hashlib.sha256(self.ana.read_bytes()).hexdigest()
        results = [intelligence.analyze(item["text"], self.katalog(), BUGUN) for item in corpus]
        assert [result.intent for result in results] == [item["intent"] for item in corpus]
        assert all(not result.safe_to_draft for item, result in zip(corpus, results) if item["intent"] != "FINANCIAL_DRAFT_REQUEST")
        assert hashlib.sha256(self.ana.read_bytes()).hexdigest() == before
        assert self.ledger() == []

    def test_safety_fail_closed_for_invalid_approval_and_corrupt_snapshot(self):
        draft = self.baslat("Gürsar Mimarlık Göktürk Okulu Devrim'e 8 bin TL ödeme yapıldı")
        draft = self.sec(draft, "hesap", "Gürsar TL Banka")
        import ui_belge_servisi as ui

        # This fixture deliberately patches the password verifier; an invalid
        # approval token still must fail before any write.
        with __import__("pytest").raises(Exception):
            ui.onayla("invalid-approval-token", "local-admin", "wrong-password")
        assert self.ledger() == []
        key = b"k" * 32
        vault = self.root / "vault" / "corrupt.vault"
        recovery_vault.create_snapshot(vault, excel_path=self.ana, sqlite_path=self.root / "finans.db", key=key)
        data = bytearray(vault.read_bytes())
        data[-1] ^= 1
        vault.write_bytes(data)
        with __import__("pytest").raises(recovery_vault.VaultError):
            recovery_vault.restore_dry_run(vault, self.root / "corrupt-restore", key=key)

    def test_synthetic_latency_benchmark_is_reproducible_without_capacity_claim(self):
        commands = [
            "Göktürk Okulu'nda Devrim'e toplam ne kadar ödeme yaptık?",
            "Gürsar Mimarlık Göktürk Okulu'nda Devrim'e 10 bin TL ödeme yaptık",
            "Göktürk Okulu'nun giderlerini kalem kalem listele",
        ] * 20
        started = time.perf_counter()
        results = [intelligence.analyze(command, self.katalog(), BUGUN) for command in commands]
        elapsed = time.perf_counter() - started
        assert len(results) == 60 and elapsed < 10
        print(f"PR15_SYNTHETIC_CORPUS cases={len(results)} extraction_seconds={elapsed:.4f}")
