"""PR-02: sentetik Excel/SQLite tutarlilik ve kurtarma sozlesmeleri.

Bu dosya canli Excel, OneDrive, gercek SQLite veya kimlik bilgisi kullanmaz.
Her test kendi gecici workbook/DB/audit/queue dosyalarinda calisir.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import excel_defter_mutabakati as mutabakat
import excel_yazici
import finans_cekirdegi as cekirdek
import finans_islem_journali as journal
import main
import pytest
import ui_belge_servisi as ui
from cfo_test_temeli import CfoTestTemeli


class FinanceConsistencyContracts(CfoTestTemeli):
    """Tum yollar sentetik fiksturle sinirlidir; uretim yoluna erisim yoktur."""

    def setUp(self):
        super().setUp()
        self.queue = self.root / "finans_sync_queue.jsonl"
        self._queue_patch = patch.object(cekirdek, "SYNC_QUEUE_PATH", self.queue)
        self._queue_patch.start()

    def tearDown(self):
        self._queue_patch.stop()
        super().tearDown()

    def belge(self, belge_id="SYN-DOC-001", tutar=10_000, cari="Devrim",
              islem="Usta/Tedarikçi Ödemesi", pb="TL", kur=1, tarih="2026-10-12"):
        kayit = {
            "Tarih": tarih,
            "Faaliyet": self.faaliyet_adi,
            "Proje": "Göktürk Okulu",
            "Cari_Tipi": "Usta",
            "Cari": cari,
            "Islem_Turu": islem,
            "Hesap_Kasa": "Gürsar TL Banka",
            "Tutar": tutar,
            "PB": pb,
            "Kur": kur,
            "Aciklama": "sentetik PR-02 kaydi",
        }
        return {
            "basari": True,
            "foto_yolu": None,
            "model_yolu": None,
            "kaynak_metin": "sentetik PR-02",
            "firma_key": "gursar_mimarlik",
            "tur": "finans_belgesi",
            "veri": {"kayitlar": [kayit]},
            "belge_id": belge_id,
            "belge_hash": (belge_id * 64)[:64],
            "dogrulama": {"durum": "PASS", "skor": 100, "hatalar": [], "uyarilar": []},
            "gecici_dosyalar": [],
        }

    def db_scalar(self, sql, params=()):
        with cekirdek._baglanti() as con:
            row = con.execute(sql, params).fetchone()
            return row[0] if row else None

    def test_a01_excel_once_db_basarisizsa_tamamlandi_denememeli(self):
        """Excel-first/DB-failure yolu artik pending kurtarma sonucu doner."""
        hazir = self.belge("A01-DB-FAIL")
        with patch.object(main, "finans_kaydi_yaz", side_effect=sqlite3.OperationalError("synthetic db down")), \
             patch.object(main.time, "sleep"):
            ok, mesaj = main.hazirlanan_belgeyi_yaz(hazir, "test")

        assert not ok
        assert "kurtarma" in mesaj.casefold()
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("A01-DB-FAIL",)) == 0
        assert self.db_scalar("SELECT state FROM finans_islemleri WHERE document_id=?", ("A01-DB-FAIL",)) == "PENDING_RECOVERY"
        assert not self.audit_olaylari("belge_tamamlandi")

    def test_a02_db_on_kontrol_basarisizsa_excel_yazilmaz(self):
        hazir = self.belge("A02-PRE-DB-FAIL")
        with patch.object(main, "fis_taslaklari", side_effect=sqlite3.OperationalError("db unavailable")):
            ok, mesaj = main.hazirlanan_belgeyi_yaz(hazir, "test")
        assert not ok
        assert "Muhasebe kontrol" in mesaj
        assert self.ledger() == []
        assert not self.queue.exists()

    def test_a03_deneme_sirasinda_sqlite_rollback_yarim_fis_birakmaz(self):
        hazir = self.belge("A03-ROLLBACK")
        cekirdek.veritabanini_hazirla()
        with cekirdek._baglanti() as con:
            con.execute(
                "CREATE TRIGGER synthetic_abort BEFORE INSERT ON fis_satirlari "
                "BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END;"
            )
        with pytest.raises(sqlite3.IntegrityError):
            cekirdek.finans_kaydi_yaz(hazir, "test")
        assert self.db_scalar("SELECT COUNT(*) FROM fisler") == 0

    def test_a04_excel_only_durum_mutabakatta_eksik_defterdir(self):
        self.gecmis_odeme_yaz("A04-EXCEL-ONLY", tutar=12_000)
        sonuc = mutabakat.kontrol()
        assert not sonuc["tutarli"]
        assert len(sonuc["eksik_defterde"]) == 1

    def test_a05_db_only_durum_mutabakatta_defter_fazlasidir(self):
        cekirdek.finans_kaydi_yaz(self.belge("A05-DB-ONLY", tutar=13_000), "test")
        sonuc = mutabakat.kontrol()
        assert not sonuc["tutarli"]
        assert any(x.startswith("A05-DB-ONLY-") for x in sonuc["defterde_fazla"])

    def test_a06_excel_tutari_degiserse_tutar_farki_raporlanir(self):
        hazir = self.belge("A06-AMOUNT")
        self.gecmis_odeme_yaz("A06-AMOUNT", tutar=10_000)
        cekirdek.finans_kaydi_yaz(hazir, "test")
        from openpyxl import load_workbook

        wb = load_workbook(self.ana)
        try:
            ws = wb["03_Kayıt Merkezi"]
            for row in range(6, 300):
                if ws[f"W{row}"].value == "A06-AMOUNT":
                    ws[f"I{row}"] = 11_000
                    break
            wb.save(self.ana)
        finally:
            wb.close()
        sonuc = mutabakat.kontrol()
        assert len(sonuc["tutar_farki"]) == 1

    def test_a07_kuyruk_kurtarma_ve_yeniden_baslatma(self):
        hazir = self.belge("A07-QUEUE")
        cekirdek.finans_kaydi_kuyruga_al(hazir, "test", "temporary")
        assert self.queue.exists()
        assert cekirdek.senkron_kuyrugunu_isle() == {"islenen": 1, "kalan": 0}
        assert not self.queue.exists()
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("A07-QUEUE",)) == 1

    def test_a08_kuyruk_hatasi_kalan_satiri_korur(self):
        self.queue.write_text("{bozuk json}\n", encoding="utf-8")
        sonuc = cekirdek.senkron_kuyrugunu_isle()
        assert sonuc == {"islenen": 0, "kalan": 1}
        assert self.queue.read_text(encoding="utf-8").strip() == "{bozuk json}"

    def test_a09_audit_incomplete_transaction_kanitini_tutar(self):
        hazir = self.belge("A09-AUDIT")
        with patch.object(main, "finans_kaydi_yaz", side_effect=RuntimeError("db down")), \
             patch.object(main.time, "sleep"):
            main.hazirlanan_belgeyi_yaz(hazir, "test")
        olaylar = self.audit_olaylari("transaction_pending_recovery")
        assert olaylar and olaylar[-1]["transaction_id"]

    def test_a10_ayni_belge_retry_idempotenttir(self):
        hazir = self.belge("A10-IDEMP", tutar=14_000)
        cekirdek.finans_kaydi_yaz(hazir, "test")
        cekirdek.finans_kaydi_yaz(hazir, "test-retry")
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("A10-IDEMP",)) == 1

    def test_a11_ayni_tutar_farkli_belge_ayri_odemedir(self):
        cekirdek.finans_kaydi_yaz(self.belge("A11-FIRST", tutar=15_000), "test")
        cekirdek.finans_kaydi_yaz(self.belge("A11-SECOND", tutar=15_000), "test")
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE cari=?", ("Devrim",)) == 2

    def test_a12_kuyruk_iki_kez_islenince_cift_posting_olmaz(self):
        hazir = self.belge("A12-QUEUE-IDEMP", tutar=16_000)
        cekirdek.finans_kaydi_kuyruga_al(hazir, "test", "retry")
        cekirdek.finans_kaydi_kuyruga_al(hazir, "test", "retry")
        sonuc = cekirdek.senkron_kuyrugunu_isle()
        assert sonuc == {"islenen": 2, "kalan": 0}
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("A12-QUEUE-IDEMP",)) == 1

    def test_a13_doviz_kuru_yoksa_kayit_reddedilir(self):
        with pytest.raises(ValueError, match="gecerli kur"):
            cekirdek.finans_kaydi_yaz(self.belge("A13-FX", tutar=100, pb="USD", kur=0), "test")
        assert self.db_scalar("SELECT COUNT(*) FROM fisler") == 0

    def test_a14_bilinmeyen_islem_turu_sessizce_gider_olmaz(self):
        with pytest.raises(ValueError, match="eşlemesi"):
            cekirdek.finans_kaydi_yaz(self.belge("A14-UNKNOWN", islem="Bilinmeyen İşlem"), "test")
        assert self.db_scalar("SELECT COUNT(*) FROM fisler") == 0

    def test_a15_es_zamanli_onayda_tek_posting_olur(self):
        taslak = self.baslat("Göktürk Okulu Devrim 17 bin ödeme yapıldı")
        taslak = self.sec(taslak, "hesap", "Gürsar TL Banka")
        token = taslak["kayit_taslagi"]["token"]
        sonuclar = []

        def onay():
            try:
                sonuclar.append(ui.onayla(token, "thread", "test-parola"))
            except (RuntimeError, ValueError) as exc:  # ikinci onay bekleyen durumda olmamalidir
                sonuclar.append(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: onay(), range(2)))
        basarili = sum(1 for x in sonuclar if isinstance(x, dict) and x.get("tamam") is True)
        assert basarili == 1
        assert self.db_scalar("SELECT COUNT(*) FROM fisler") == 1

    def test_fin_p01_mevcut_excel_db_ayrisini_kanitlar(self):
        """PASS diagnostic: mevcut Excel-ilk akisinda DB kopugu sessiz ayrisma uretiyor."""
        hazir = self.belge("FIN-P01-DIAG", tutar=18_000)
        with patch.object(main, "finans_kaydi_yaz", side_effect=sqlite3.OperationalError("synthetic db down")), \
             patch.object(main.time, "sleep"):
            ok, mesaj = main.hazirlanan_belgeyi_yaz(hazir, "diagnostic")

        assert ok is False
        assert "kurtarma" in mesaj.casefold()
        assert [x for x in self.ledger() if str(x.get("Kaynak_Belge_ID", "")).startswith("FIN-P01-DIAG-")]
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("FIN-P01-DIAG",)) == 0
        assert not self.queue.exists()
        assert self.db_scalar("SELECT state FROM finans_islemleri WHERE document_id=?", ("FIN-P01-DIAG",)) == "PENDING_RECOVERY"
        assert not self.audit_olaylari("belge_tamamlandi")
        rapor = mutabakat.kontrol()
        assert not rapor["tutarli"]
        assert len(rapor["eksik_defterde"]) == 1
        assert rapor["eksik_defterde"][0]["excel_tutar"] == 18_000

    def _crash_process_files(self, belge_id):
        """Alt surec icin tamamen sentetik env ve script hazirlar."""
        child_root = self.root / "subprocess-crash"
        child_root.mkdir(parents=True, exist_ok=True)
        ana = child_root / "ANA.xlsx"
        yedek = child_root / "ANA_backup.xlsx"
        shutil.copy2(self.ana, ana)
        shutil.copy2(self.ana, yedek)
        hazir_path = child_root / "hazir.json"
        hazir_path.write_text(json.dumps(self.belge(belge_id, tutar=19_000), ensure_ascii=False), encoding="utf-8")
        script = child_root / "child.py"
        repo = Path(__file__).resolve().parents[1]
        script.write_text(textwrap.dedent(f"""
            import json, os, sqlite3, sys
            from pathlib import Path
            sys.path.insert(0, {str(repo / 'scripts')!r})

            mode = sys.argv[1]
            belge_id = {belge_id!r}
            if mode == "crash":
                import main
                hazir = json.loads(Path({str(hazir_path)!r}).read_text(encoding="utf-8"))
                original_yaz = main.yaz
                def crash_after_excel(*args, **kwargs):
                    result = original_yaz(*args, **kwargs)
                    if result.get("basari"):
                        os._exit(47)
                    return result
                main.yaz = crash_after_excel
                main.hazirlanan_belgeyi_yaz(hazir, "subprocess-crash")
                raise AssertionError("crash enjeksiyonu tetiklenmedi")

            if mode == "recover":
                from finans_islem_journali import recover_pending_transactions
                print(json.dumps(recover_pending_transactions(), ensure_ascii=False))
                raise SystemExit(0)

            import config
            from openpyxl import load_workbook
            workbook = load_workbook(config.ana_excel_yolu(), read_only=True, data_only=False)
            try:
                ws = workbook["03_Kayıt Merkezi"]
                kaynak_index = ord(config.FIRMALAR["touche_holding"]["ana_ledger"]["sutunlar"]["Kaynak_Belge_ID"]) - ord("A")
                excel_count = sum(1 for row in ws.iter_rows(min_row=6, max_col=kaynak_index + 1, values_only=True)
                                  if str(row[kaynak_index] or "").startswith(f"{{belge_id}}-"))
            finally:
                workbook.close()
            db_count = 0
            if config.FINANS_DB.exists():
                with sqlite3.connect(config.FINANS_DB) as con:
                    db_count = con.execute("SELECT COUNT(*) FROM fisler WHERE belge_id=?", (belge_id,)).fetchone()[0]
                    journal = con.execute("SELECT state FROM finans_islemleri WHERE document_id=?", (belge_id,)).fetchall()
            else:
                journal = []
            queue = Path(config.LOGLAR) / "finans_sync_queue.jsonl"
            audit = Path(config.AUDIT_LOG)
            olaylar = []
            if audit.exists():
                olaylar = [json.loads(line).get("olay") for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
            print(json.dumps({{"excel_count": excel_count, "db_count": db_count,
                               "journal": [row[0] for row in journal],
                               "queue_exists": queue.exists(), "completed": "belge_tamamlandi" in olaylar,
                               "audit_events": olaylar}}, ensure_ascii=False))
        """), encoding="utf-8")
        env = os.environ.copy()
        env.update({
            "TOUCHE_BASE_DIR": str(child_root),
            "TOUCHE_MAIN_WORKBOOK": str(ana),
            "TOUCHE_BACKUP_WORKBOOK": str(yedek),
        })
        return child_root, script, env

    def test_b01_ayri_surec_cokmesinde_excel_db_ayrisini_kanitlar(self):
        """PASS diagnostic: Excel replacement sonrasi process kesilirse ikinci surec ayrismayi gorur."""
        belge_id = "B01-SUBPROCESS-CRASH"
        child_root, _script, env = self._crash_process_files(belge_id)
        result = subprocess.run(
            [sys.executable, str(child_root / "child.py"), "crash", belge_id],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert result.returncode == 47, result.stderr or result.stdout
        inspect = subprocess.run(
            [sys.executable, str(child_root / "child.py"), "inspect", belge_id],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert inspect.returncode == 0, inspect.stderr or inspect.stdout
        durum = json.loads(inspect.stdout.strip().splitlines()[-1])
        assert durum["excel_count"] == 1
        assert durum["db_count"] == 0
        assert durum["journal"] == ["PREPARED"], durum
        assert durum["queue_exists"] is False
        assert durum["completed"] is False

    def test_b02_ayri_surec_cokmesi_kalici_kurtarma_isareti_uretmelidir(self):
        """Ani kesilme sonrasi ikinci surec Excel'den SQLite'a guvenle devam eder."""
        belge_id = "B02-SUBPROCESS-RECOVERY"
        child_root, _script, env = self._crash_process_files(belge_id)
        result = subprocess.run(
            [sys.executable, str(child_root / "child.py"), "crash", belge_id],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert result.returncode == 47
        recovered = subprocess.run(
            [sys.executable, str(child_root / "child.py"), "recover", belge_id],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        assert recovered.returncode == 0, recovered.stderr or recovered.stdout
        inspect = subprocess.run(
            [sys.executable, str(child_root / "child.py"), "inspect", belge_id],
            cwd=str(self.root), env=env, capture_output=True, text=True, timeout=20, check=False,
        )
        durum = json.loads(inspect.stdout.strip().splitlines()[-1])
        assert durum["excel_count"] == 1
        assert durum["db_count"] == 1, recovered.stdout + recovered.stderr
        assert durum["completed"] is False
        assert "transaction_committed" in durum["audit_events"]

    def test_a16_ayni_belge_id_farkli_icerik_cakismasi_reddedilir(self):
        ilk = self.belge("A16-CONTENT-CONFLICT", tutar=20_000)
        cekirdek.finans_kaydi_yaz(ilk, "test")
        ikinci = self.belge("A16-CONTENT-CONFLICT", tutar=21_000)
        with pytest.raises(ValueError, match="farklı içerikle"):
            cekirdek.finans_kaydi_yaz(ikinci, "retry-farkli")
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("A16-CONTENT-CONFLICT",)) == 1

    def _journal_state(self, document_id):
        return self.db_scalar("SELECT state FROM finans_islemleri WHERE document_id=?", (document_id,))

    def _pending_transaction(self, document_id):
        hazir = self.belge(document_id, tutar=23_000)
        result = journal.execute_transaction(
            hazir, "test", excel_writer=excel_yazici.yaz,
            db_writer=lambda *_args: (_ for _ in ()).throw(sqlite3.OperationalError("synthetic db unavailable")),
        )
        assert result["status"] == "PENDING_RECOVERY"
        return hazir

    def test_b03_sqlite_commit_sonrasi_journal_guncellenmeden_kurtarilir(self):
        hazir = self.belge("B03-DB-COMMIT-CRASH", tutar=24_000)

        def commit_then_crash(data, actor):
            cekirdek.finans_kaydi_yaz(data, actor)
            raise RuntimeError("synthetic crash after SQLite commit")

        result = journal.execute_transaction(
            hazir, "test", excel_writer=excel_yazici.yaz, db_writer=commit_then_crash,
        )
        assert result["status"] == "PENDING_RECOVERY"
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B03-DB-COMMIT-CRASH",)) == 1
        recovered = journal.recover_pending_transactions()
        assert recovered["committed"] == 1
        assert self._journal_state("B03-DB-COMMIT-CRASH") == "COMMITTED"
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B03-DB-COMMIT-CRASH",)) == 1

    def test_b04_excel_disaridan_degistiğinde_otomatik_kayit_yapilmaz(self):
        hazir = self.belge("B04-EXTERNAL-CHANGE", tutar=25_000)

        def writer(*args, **kwargs):
            result = excel_yazici.yaz(*args, **kwargs)
            from openpyxl import load_workbook
            wb = load_workbook(self.ana)
            try:
                ws = wb["03_Kayıt Merkezi"]
                for row in range(6, 300):
                    if str(ws[f"Z{row}"].value or "").startswith("B04-EXTERNAL-CHANGE-"):
                        ws[f"I{row}"] = 99_999
                        break
                wb.save(self.ana)
            finally:
                wb.close()
            return result

        result = journal.execute_transaction(hazir, "test", excel_writer=writer)
        assert result["status"] == "NEEDS_REVIEW"
        assert self._journal_state("B04-EXTERNAL-CHANGE") == "NEEDS_REVIEW"
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B04-EXTERNAL-CHANGE",)) == 0

    def test_b05_recovery_dry_run_yazmaz(self):
        self._pending_transaction("B05-DRY-RUN")
        before = self._journal_state("B05-DRY-RUN")
        report = journal.recovery_dry_run()
        assert report["dry_run"] is True
        assert any(x["transaction_id"] for x in report["sonuclar"])
        assert self._journal_state("B05-DRY-RUN") == before == "PENDING_RECOVERY"
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B05-DRY-RUN",)) == 0

    def test_b06_kurtarma_iki_kez_ve_es_zamanli_cift_fis_uretmez(self):
        self._pending_transaction("B06-RECOVERY-IDEMP")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: journal.recover_pending_transactions(), range(2)))
        assert all(x["needs_review"] == 0 for x in results)
        assert self._journal_state("B06-RECOVERY-IDEMP") == "COMMITTED"
        assert self.db_scalar("SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B06-RECOVERY-IDEMP",)) == 1
        assert journal.recover_pending_transactions()["islem_sayisi"] == 0

    def test_b07_bozuk_journal_needs_review_olarak_kapanir(self):
        self._pending_transaction("B07-CORRUPT-JOURNAL")
        with cekirdek._baglanti() as con:
            con.execute("UPDATE finans_islemleri SET expected_excel_ids=? WHERE document_id=?", ("{bozuk", "B07-CORRUPT-JOURNAL"))
        report = journal.recover_pending_transactions()
        assert report["needs_review"] == 1
        assert self._journal_state("B07-CORRUPT-JOURNAL") == "NEEDS_REVIEW"

    def test_b08_audit_yazilamazsa_onarim_kaniti_korunur(self):
        hazir = self.belge("B08-AUDIT-REPAIR", tutar=26_000)
        with patch.object(journal, "audit_yaz", side_effect=OSError("audit unavailable")):
            result = journal.execute_transaction(hazir, "test", excel_writer=excel_yazici.yaz)
        assert result["status"] == "COMMITTED"
        assert self.db_scalar("SELECT audit_repair_required FROM finans_islemleri WHERE document_id=?", ("B08-AUDIT-REPAIR",)) == 1

    def test_b09_mutabakat_kurtarma_sirasinda_uyarir_sonra_duzelir(self):
        self._pending_transaction("B09-RECON-RECOVERY")
        assert mutabakat.kontrol()["tutarli"] is False
        journal.recover_pending_transactions()
        assert mutabakat.kontrol()["tutarli"] is True

    def test_b10_prepared_restart_guvenli_aborted_sonucu_uretmez(self):
        hazir = self.belge("B10-PREPARED-RESTART", tutar=27_000)
        tx = journal.prepare_transaction(hazir, "test")
        assert tx["state"] == "PREPARED"
        dry = journal.recovery_dry_run()
        assert dry["sonuclar"][0]["status"] == "ABORTED"
        assert self._journal_state("B10-PREPARED-RESTART") == "PREPARED"
        report = journal.recover_pending_transactions()
        assert report["committed"] == 0
        assert self._journal_state("B10-PREPARED-RESTART") == "ABORTED"

    def test_b11_excel_fingerprint_persist_edilmeden_cokme_guvenli_kurtarilir(self):
        """PREPARED + tam Excel satırı, hash doğrulanmadan SQLite'a yazılamaz."""
        hazir = self.belge("B11-FINGERPRINT-BEFORE", tutar=28_000)
        journal.prepare_transaction(hazir, "test")
        excel_yazici.yaz(
            hazir["firma_key"], hazir["veri"], hazir["belge_id"], aktor="test"
        )
        report = journal.recover_pending_transactions()
        assert report["committed"] == 1
        assert self._journal_state("B11-FINGERPRINT-BEFORE") == "COMMITTED"

    def test_b12_restart_oncesi_excel_tutari_degiserse_needs_review(self):
        self._pending_transaction("B12-EXCEL-AMOUNT-CHANGE")
        from openpyxl import load_workbook

        wb = load_workbook(self.ana)
        try:
            ws = wb["03_Kayıt Merkezi"]
            for row in range(6, 300):
                if str(ws[f"Z{row}"].value or "").startswith("B12-EXCEL-AMOUNT-CHANGE-"):
                    ws[f"I{row}"] = 99_999
                    break
            wb.save(self.ana)
        finally:
            wb.close()
        report = journal.recover_pending_transactions()
        assert report["needs_review"] == 1
        assert self._journal_state("B12-EXCEL-AMOUNT-CHANGE") == "NEEDS_REVIEW"
        assert self.db_scalar(
            "SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B12-EXCEL-AMOUNT-CHANGE",)
        ) == 0

    def test_b13_sqlite_tutari_degiserse_ayni_voucher_id_yeterli_degil(self):
        hazir = self.belge("B13-SQLITE-AMOUNT-CHANGE", tutar=29_000)

        def commit_then_crash(data, actor):
            cekirdek.finans_kaydi_yaz(data, actor)
            raise RuntimeError("synthetic crash after SQLite commit")

        result = journal.execute_transaction(
            hazir, "test", excel_writer=excel_yazici.yaz, db_writer=commit_then_crash
        )
        assert result["status"] == "PENDING_RECOVERY"
        with cekirdek._baglanti() as con:
            con.execute(
                "UPDATE fis_satirlari SET borc_kurus=borc_kurus+1 "
                "WHERE fis_id=? AND satir_no=1",
                ("JRN-B13-SQLITE-AMOUNT-CHANGE-001",),
            )
        report = journal.recover_pending_transactions()
        assert report["needs_review"] == 1
        assert self._journal_state("B13-SQLITE-AMOUNT-CHANGE") == "NEEDS_REVIEW"

    def test_b14_needs_review_baslangicta_asla_otomatik_post_etmez(self):
        self._pending_transaction("B14-REVIEW-NO-REPOST")
        with cekirdek._baglanti() as con:
            con.execute(
                "UPDATE finans_islemleri SET expected_excel_fingerprint=? WHERE document_id=?",
                ("bad-fingerprint", "B14-REVIEW-NO-REPOST"),
            )
        first = journal.recover_pending_transactions()
        second = journal.recover_pending_transactions()
        assert first["needs_review"] == second["needs_review"] == 1
        assert self._journal_state("B14-REVIEW-NO-REPOST") == "NEEDS_REVIEW"
        assert self.db_scalar(
            "SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B14-REVIEW-NO-REPOST",)
        ) == 0

    def test_b15_dry_run_db_ve_dosyalari_byte_byte_ayni_kalir(self):
        self._pending_transaction("B15-DRY-RUN-BYTES")
        paths = [self.ana, self.yedek, self.root / "finans.db", self.audit]
        before = {str(path): path.read_bytes() if path.exists() else None for path in paths}
        journal.recovery_dry_run()
        after = {str(path): path.read_bytes() if path.exists() else None for path in paths}
        assert after == before

    def test_b16_audit_onarimi_finansal_postingi_tekrarlamaz(self):
        hazir = self.belge("B16-AUDIT-REPAIR", tutar=30_000)
        with patch.object(journal, "audit_yaz", side_effect=OSError("audit unavailable")):
            result = journal.execute_transaction(hazir, "test", excel_writer=excel_yazici.yaz)
        assert result["status"] == "COMMITTED"
        tx_id = result["transaction_id"]
        before_count = self.db_scalar(
            "SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B16-AUDIT-REPAIR",)
        )
        repaired = journal.repair_audit(tx_id)
        assert repaired["repaired"] is True
        assert self.db_scalar(
            "SELECT COUNT(*) FROM fisler WHERE belge_id=?", ("B16-AUDIT-REPAIR",)
        ) == before_count
        assert self.db_scalar(
            "SELECT audit_repair_required FROM finans_islemleri WHERE transaction_id=?", (tx_id,)
        ) == 0
        assert journal.repair_audit(tx_id)["repaired"] is False

    def test_b17_es_zamanli_farkli_icerikli_prepare_belge_kimligi_cakisir(self):
        def prepare(amount):
            try:
                return journal.prepare_transaction(
                    self.belge("B17-CONCURRENT-DOCUMENT", tutar=amount), "test"
                )
            except Exception as exc:  # noqa: BLE001 - conflict is the assertion
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(prepare, (31_000, 32_000)))
        successes = [item for item in results if isinstance(item, dict)]
        failures = [item for item in results if isinstance(item, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)
        assert self.db_scalar(
            "SELECT COUNT(*) FROM finans_islemleri WHERE document_id=?",
            ("B17-CONCURRENT-DOCUMENT",),
        ) == 1

    def test_b18_needs_review_yetkisiz_kurtarma_reddedilir_ve_owner_acikca_onaylar(self):
        self._pending_transaction("B18-MANUAL-REVIEW")
        with cekirdek._baglanti() as con:
            con.execute(
                "UPDATE finans_islemleri SET state='NEEDS_REVIEW' WHERE document_id=?",
                ("B18-MANUAL-REVIEW",),
            )
        tx_id = self.db_scalar(
            "SELECT transaction_id FROM finans_islemleri WHERE document_id=?",
            ("B18-MANUAL-REVIEW",),
        )
        with pytest.raises(PermissionError, match="doğrulanmış oturum"):
            journal.authorize_needs_review(tx_id, "forged-admin", "manuel kurtarma kararı")
        viewer = {
            "yetkili": True, "kimlik": "viewer", "rol": "VIEWER",
            "faaliyetler": ["*"], "projeler": ["*"],
        }
        with pytest.raises(PermissionError):
            journal.authorize_needs_review(tx_id, "viewer", "Bu işlem incelendi", authorization=viewer)
        owner = {
            "yetkili": True, "kimlik": "owner", "rol": "OWNER",
            "faaliyetler": ["*"], "projeler": ["*"],
        }
        result = journal.authorize_needs_review(
            tx_id, "owner", "Excel ve journal kanıtları manuel olarak doğrulandı", authorization=owner
        )
        assert result["status"] == "COMMITTED"
        assert self._journal_state("B18-MANUAL-REVIEW") == "COMMITTED"

    def test_b19_recovery_health_canli_durumlari_ve_startup_hatasini_gosterir(self):
        self._pending_transaction("B19-HEALTH")
        health = journal.recovery_health()
        assert health["durum"] == "degraded"
        assert health["pending_recovery"] == 1
        with cekirdek._baglanti() as con:
            con.execute(
                "UPDATE finans_islemleri SET state='NEEDS_REVIEW', audit_repair_required=1 "
                "WHERE document_id=?", ("B19-HEALTH",)
            )
        health = journal.recovery_health()
        assert health["needs_review"] == 1
        assert health["audit_repair_required"] == 1
        failed = journal.recovery_health(startup_ready=False, startup_error="synthetic startup failure")
        assert failed["durum"] == "degraded"
        assert failed["hata"] == "synthetic startup failure"

