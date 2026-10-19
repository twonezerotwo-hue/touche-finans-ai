# TESTING

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q -rs                   # whole suite with explicit skip reasons
ruff check scripts tests --select F,E9   # lint used in this repo
python -m mypy scripts/excel_deposu.py scripts/excel_operasyon.py scripts/excel_defter_mutabakati.py \
    scripts/cfo_niyet.py scripts/cfo_sohbet.py scripts/cfo_sorgu.py scripts/cfo_varliklar.py \
    scripts/cfo_yonlendirici.py scripts/proje_finansi.py scripts/excel_yazici.py scripts/cfo_eslestirme.py \
    scripts/cfo_dil.py                                                                                         # clean
```
Windows: `py -3.12 -m pytest -q`. The old `python -m unittest discover -s tests` also works for the non-browser tests.

Result at the time of writing: **408+ passed, 2 skipped** (a skip needs a copy of a production DB, see below).
Baseline on the original commit: 6 modules failed at import; with that worked around, 126 passed / 21 failed.

## PR-01 CI baseline

GitHub Actions runs the isolated suite on Python 3.12 under Ubuntu and Windows, then attempts Chromium browser tests when
the runner can provide a browser. Ruff and mypy are intentionally reported as non-blocking baseline diagnostics in PR-01;
the existing tree has known debt (recorded in the audit) and application logic is not rewritten just to hide it. Later PRs
will ratchet these checks to blocking gates module by module. The dependency audit and production-path/secret scan also run
on every push and pull request. CI always redirects `TOUCHE_*` paths to `.ci-runtime` and never receives production files.

The first GitHub Actions run for PR-01 was `39116113782`: Ubuntu completed with **408 passed / 3 skipped**, Windows with
**409 passed / 2 skipped**, and dependency audit completed. The secret scan failed because its own workflow comment contained
the path pattern it scans. This was corrected without weakening the scanner. The dependency audit is now blocking for known
vulnerabilities; the `excel-parser` VCS dependency is reported separately and is explicitly not considered audited.

After the fix, run `39121924145` completed successfully. Exact skipped tests were:

* Ubuntu: `tests/test_acceptance.py:300` (production DB copy not configured),
  `tests/test_cfo_borc_eslestirme.py:400` (Windows-only Excel sharing lock), and
  `tests/test_phase1.py:230` (optional `pdftoppm`/Poppler unavailable).
* Windows: `tests/test_acceptance.py:300` (production DB copy not configured) and
  `tests/test_phase1.py:230` (optional `pdftoppm`/Poppler unavailable).

The successful run recorded **408 passed / 3 skipped / 352 subtests** on Ubuntu and
**409 passed / 2 skipped / 352 subtests** on Windows. Dependency audit and secret/path scan both passed.

## PR-06 final product acceptance

`tests/test_final_product_acceptance.py` is the synthetic product-acceptance contract. It verifies the
deterministic CFO instruction → draft → password approval → Excel/SQLite posting → accounting report →
encrypted recovery-vault dry-run path; supplier payment, collection, accrual, revenue, loan/FX and
intercompany fixtures; duplicate retry versus a separate identical payment; locked-workbook degradation;
scope-limited identity; report consistency; and a measured synthetic workload. The workload assertion is
only a regression alarm (30 seconds on the CI runner), not a production performance claim. All files are
created under the per-test temporary directory and are removed after the test.

PR-06 local result: **456 passed, 8 skipped, 352 subtests passed in 136.00s**.
The 8 skips were one opt-in production-DB-copy migration test and seven Chromium
tests because a browser binary was unavailable in this environment. PR-06 CI run
`39177361390` passed on Ubuntu and Windows; post-merge main run `39177888177`
also passed on Ubuntu (1m51s), Windows (4m35s), dependency audit and secret/path scan.

## PR-09 final release-readiness acceptance

`tests/test_final_release_readiness.py` adds five repeatable disposable-fixture
contracts: the Windows acceptance adapter, complete encrypted restore with
synthetic RPO/RTO measurement, wrong/missing/damaged-key and atomic-replace
failure paths, manual `NEEDS_REVIEW` recovery with OWNER/CFO audit evidence,
and the recovery permission matrix. No production path is resolved by these
tests.

Local PR-09 focused result: **5 passed**. Final local full suite:
**461 passed, 8 skipped, 352 subtests** in 142.09 seconds. PR-09 CI run
`39197157398` passed with **466 passed, 3 skipped, 352 subtests** on Ubuntu and
**467 passed, 2 skipped, 352 subtests** on Windows. Chromium was installed and
browser E2E ran in both CI jobs. Ubuntu skips were the opt-in production-DB copy,
the Windows-only sharing-lock test and optional Poppler; Windows skips were the
production-DB copy and optional Poppler. A local machine without a browser is
reported as `SKIPPED`, never silently treated as passed.

The Windows adapter is executed against a temporary synthetic workbook. The
optional `--onedrive-klasoru` mode was not run: live OneDrive synchronization is
**NOT VERIFIED** and remains a rollout gate.

### Final status vocabulary

Every final acceptance item is reported as `PASS`, `FAIL`, `SKIPPED`,
`NOT VERIFIED`, or `BLOCKED`. A synthetic pass does not prove production
OneDrive, real Excel, owner policy approval, off-site storage, or live migration.

## Production data is never touched

## PR-16 financial correctness contracts

`tests/test_finance_correctness_v2.py` tamamen geçici SQLite kullanır ve beş
regresyon sözleşmesi içerir:

* işlem tarihi forecast başlangıcından önce, vade tarihi pencere içinde olan
  alacak projeksiyona dahil edilir;
* vade penceresi dışındaki kayıt ve iptal edilmiş kayıt projeksiyona girmez;
* desteklenmeyen para tutarı veya voucher kimliği içeren LLM yanıtı reddedilir;
* yalnızca yetkili sentetik kanıtla eşleşen LLM yanıtı kabul edilir.

PR-16 focused result: **12 passed** (forecast + provenance ve mevcut
provenance sözleşmeleri). Ruff `E9,F` ve `mypy scripts/finans_provenance.py`
temizdir. `finans_danismani.py` içindeki önceki non-blocking Ruff/mypy borcu
ayrı tutulmuştur; bu PR ile yeni bir borç eklenmemiştir.

`nakit_13hafta._kirilim()` artık işlem tarihini değil `vade` alanını pencere
filtresi olarak kullanır ve iptal/ters kayıtları dışarıda bırakır. Bu, açılış
nakdini veya geçmiş muhasebe kayıtlarını değiştirmez; yalnızca gelecek nakit
olaylarının doğru haftaya alınmasını sağlar.

* `tests/conftest.py` sets `TOUCHE_BASE_DIR`, `TOUCHE_MAIN_WORKBOOK`, `TOUCHE_BACKUP_WORKBOOK` to a temp folder
  **before `config` is imported** and aborts the session if the resolved MAIN path is outside it or looks like a
  user/OneDrive folder.
* Every test class builds its own workbook with `tests/yardimci_excel.py` (same sheet/column contract as the
  real Smart Excel, fictitious names/amounts) and routes MAIN/BACKUP to it (`tests/_ortam.py`). The DB, audit log,
  approval queue and wizard state are patched to temp files (`tests/cfo_test_temeli.py`).
* LLM/network calls raise `AssertionError` in the CFO tests (`llm_cagrilamaz`), so a test that accidentally calls a
  model fails loudly.
* `TOUCHE_URETIM_DB_KOPYASI=<copy of finans.db>` opts in to one acceptance check against a **copy**; never point it at
  the live file.
* A fake `TELEGRAM_BOT_TOKEN` is set so `telegram_bot` can be imported (no network use).

## Map

| File | What it proves |
|---|---|
| `test_excel_deposu.py` | one MAIN + one BACKUP (backup refreshed, never multiplied), atomic replace and rollback, no stale temp files, formulas/existing values/partial rows protected, external modification detected, corrupted MAIN never overwrites BACKUP, chart/embedded-part loss rejected, dry run, lock re-entrancy, restore = swap and only with `GERI-DON`, no autonomous layer can call restore, audit trail, **static invariants** (only `excel_deposu` saves a workbook; no timestamped-backup code; no hard-coded workbook paths) |
| `test_excel_operasyon.py` | inventory roles, only proven redundant generated copies are deletable, deletion needs the `SIL` word, status/validate |
| `test_excel_mutabakati.py` | Excel↔ledger reconciliation is read-only, reports manual rows / amount changes / orphans, import skips app-written rows (no double count), idempotent |
| `test_muhasebe_eslemesi.py` | payment ≠ expense, accrual, collection ≠ income, loan ≠ income, intercompany never an expense, unknown type never silently an expense |
| `test_cfo_sohbet.py` | sentence parsing (amount/date/currency forms, name variants, "Gürsar İnşaat"="Gürsar Mimarlık"), query vs record, complex/unsupported instructions, wizard (no field re-asked, typed answer = chip click, ambiguity, cancel, ownership), approval writes one record with the original instruction in the audit, double approval, repeat guard, **no LLM call in any step** |
| `test_cfo_dil.py` | Turkish language helpers without a workbook: written-out amounts (`yirmi beş bin`, `10 bin 500`), relative dates (`geçen cuma`, `3 gün önce`, `ayın 5'inde`), query periods (`bu hafta`, `son 30 gün`, `ilk çeyrek`, ranges), negated/needed/conditional/future mood, account hints |
| `test_cfo_anlama.py` | everyday-Turkish understanding end to end: verb variety, direction (customer paying = collection, `fatura kestik` vs `fatura geldi`), amounts/dates/currency symbols, account hints, typos and consonant softening, sentences that must **not** open a draft (`ödeme yapmadık`, `yapmam lazım`, `gerekirse`, `istiyor`, `3 rapor verdik`), query metrics/periods/direction; wizard keeps the original sentence in the audit trail and the bank/cash hint after the activity is answered |
| `test_cfo_sorgu.py` | project/activity/counterparty/holding questions, period handling, ambiguity never guessed, **zero mutation** (Excel hash, backup, ledger row counts, temp files), "why is this record here" |
| `test_proje_finansi.py` | revenue/collection/expense/payment/profit/cash kept apart on fixtures, other projects don't leak, work items, counterparty balances, reversals, loans, read-only connection, Holding built from real activities with intercompany eliminated, a newly added activity works unchanged |
| `test_cfo_http.py` | the same flows over real HTTP (wizard, password approval, queries, navigation data endpoints, RBAC scope, other users' drafts, Excel status) |
| `test_final_product_acceptance.py` | PR-06 end-to-end financial acceptance: approval, durable posting, reports, encrypted backup/restore, realistic transaction matrix, idempotency, lock/health degradation and scoped identity |
| `test_final_release_readiness.py` | PR-09 synthetic Windows adapter, complete encrypted restore, RPO/RTO measurement, fail-closed vault paths, manual recovery audit/authorization and RBAC matrix |
| `test_final_operational_closure.py` | Migration/rollback rehearsal, fail-closed offsite emulator, external workbook conflict rejection and 10/100/500-record synthetic snapshot/restore performance |
| `test_ui_tarayici.py` | real Chromium (Playwright): Holding → Gürsar Mimarlık → Göktürk Okulu navigation, back button, deep link after reload, breadcrumb, wizard chips, the 10-step end-to-end scenario, and **no console/CSP error** on any page. Skipped automatically when Playwright/Chromium is missing |
| existing suites | legacy behaviour (documents, Telegram flows, FX, RBAC, project work items, …) migrated to the isolated environment |

## Windows / OneDrive validation (run on the real machine)

`py -3.12 scripts\windows_dogrulama.py [--onedrive-klasoru "%USERPROFILE%\OneDrive\Desktop\Touche"]` runs the
scenarios against **TEST workbooks only** (it creates and removes its own folders, hashes the production MAIN/BACKUP before
and after, and fails if they changed): closed-Excel write, V1→V2→V3 with BACKUP always one version behind, only MAIN+BACKUP on
disk, no leftover temp files (also not in the OneDrive folder), failure recovery, **Excel-open lock (real Win32 share-mode
lock, Windows only; SKIP elsewhere)** with BACKUP not advancing, retry after Excel is closed, restore = swap, and the audit
trail. Exit code 0 only when everything is PASS. `tests/test_windows_dogrulama.py` runs the tool in CI.

## Browser tests

`pip install playwright`. The browser binary is found via `TOUCHE_TEST_CHROMIUM`, else
`$PLAYWRIGHT_BROWSERS_PATH/chromium_headless_shell-*/chrome-linux/headless_shell` (default `/opt/pw-browsers`).
Do not run `playwright install` in a locked-down environment; point at an existing binary.
The tests start the real server on a free port against a temp workbook.

## End-to-end scenario (isolated workbook, real browser)

Steps 1–4 (Holding → Gürsar Mimarlık → projects → Göktürk Okulu, back button, deep link, breadcrumb):
`test_holding_faaliyet_proje_tiklanir_ve_geri_calisir`, `test_projeler_menusunden_proje_acilir`.

Steps 5–10: `test_tam_senaryo_onay_sorgu_tekrar_ve_eksik_komut` — full command → draft with
Faaliyet/Proje/İşlem/Cari/Tutar (Excel unchanged) → password approval → exactly one new Excel row →
"Devrim'e toplam ne kadar ödeme yaptık?" answered from the ledger with no write → the identical command is stopped
with "Yine de ekle / Vazgeç" (nothing written, "Vazgeç" confirmed) → "5 bin gider ekle." is completed by chip
clicks (Faaliyet → Proje → İş kalemi → Cari), the 5.000 TL amount is never asked again, the draft appears and the
workbook is still unchanged because it was not approved.

Wizard from "10 bin ödeme yapıldı.": `test_eksik_komut_sihirbazi_tutari_tekrar_sormaz`; project-page "Ödeme ekle"
opens the wizard with activity and project already filled: `test_projeden_yeni_odeme_asistani_acar`.

## What is *not* covered
* The Windows file-lock (`msvcrt`) and real OneDrive/Excel-open behaviour are simulated (permission error at replace);
  they were not exercised on Windows here.
* The legacy LLM-assisted path for complex instructions needs a running model; only its gating is tested.
* The production workbook was never available to the tests (by design).

## Final operational closure evidence

`tests/test_final_operational_closure.py` yeni kanıttır; PR-09 sonucu tekrar
sayılmaz. Windows'ta **8 test geçti**: additive migration ve ikinci çalıştırma,
duplicate document-id fail-closed, checksum doğrulayan local offsite emulator,
external workbook change rejection ve 10/100/500 kayıt sentetik snapshot/
restore performansı. İlk çalıştırmada Windows temporary-file descriptor kusuru
yakalandı; descriptor kapatma ve metadata atomic replace düzeltildikten sonra
focused suite `8 passed` oldu. Final local full suite `469 passed, 8 skipped,
352 subtests` olarak tamamlandı.

Ölçümler regresyon alarmıdır; üretim kapasitesi/RPO/RTO garantisi değildir.
Gerçek Excel desktop share-mode, OneDrive cloud sync, canlı migration ve gerçek
off-site provider **NOT VERIFIED** olarak kalır.

## PR-14 operational resilience v2

`tests/test_recovery_operations_v2.py` yalnız `tmp_path` altında çalışır ve
şunları sözleşme olarak doğrular: 32-byte sentetik key üretimi/rotasyonu ve
revoke; bounded retry/backoff; off-site manifest/ciphertext checksum hatası;
stale/missing backup ve recovery/audit backlog sağlık uyarıları; dış değişiklik
çakışması; atomic replace ve temporary-file cleanup. Bu testler gerçek KMS,
OneDrive, Excel desktop veya off-site hesabı kullanmaz.

Focused validation:

```powershell
py -3.12 -m pytest -q tests/test_recovery_operations_v2.py tests/test_final_operational_closure.py
```

Status labels: **ENGINEERING_TESTED** = disposable fixture/contract,
**SANDBOX_VERIFIED** = local emulator, **PRODUCTION_NOT_VERIFIED** = real
OneDrive/Excel/KMS/provider, **OWNER_APPROVAL_REQUIRED** = key custody,
escrow, retention, scheduler and restore authorization.

## PR-15 final acceptance

`tests/test_finance_v2_final_acceptance.py` sentetik uçtan uca finans akışını,
Türkçe korpus güvenli ayrımını, invalid approval/corrupt vault fail-closed
davranışını ve parser latency regresyon alarmını doğrular. Focused sonuç
**4 passed**. Gerçek Ollama/Qwen inference, gerçek Excel/OneDrive ve canlı
KMS/provider bu kabulde çalıştırılmadı: **NOT VERIFIED**.

## PR-12 CFO + belge UX v2

`ui/onboarding.js` ilk kullanım turunu finansal veriden bağımsız yürütür. Tur;
dashboard, CFO, belge/onay, faaliyet-proje ve recovery görünümünü açıklar.
`Şimdilik geç`, `Bir daha gösterme` ve ileri/sonlandır seçenekleri vardır;
tercih yalnızca tarayıcı localStorage alanında tutulur. `Yardım turunu aç`
ile tekrar başlatılabilir. `tests/test_onboarding_contract.py` bu davranışın
API çağrısı veya finansal yazma yapmadığını doğrular; gerçek tarayıcı davranışı
Playwright varsa `test_ui_tarayici.py` ile ayrıca çalışır.
