# TESTING

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                      # whole suite (~40 s)
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

## Production data is never touched

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
