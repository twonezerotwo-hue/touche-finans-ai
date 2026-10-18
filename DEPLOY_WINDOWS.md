# DEPLOY_WINDOWS — update `C:\ai-excel` from `main` and clean the Smart Excel environment

Goal state: **one source tree** (`C:\ai-excel`, equal to GitHub `main`), **one MAIN + one BACKUP workbook**, history in the
audit log / ledger, every old generated Excel copy removed **only after it is proven redundant**.

Everything below is read-only until a step says otherwise. Every deletion is gated by a proof, a confirmation word and an
audit record. If any step hits a **STOP** condition, stop and report; do not improvise a destructive action.

> Never: `git reset --hard`, `git clean -fd`, `git checkout -f`, `git push --force`, deleting an Excel file by hand,
> touching an Excel file that is not Smart Excel related, creating `C:\ai-excel-new` / a second checkout / a second MAIN.

All commands: PowerShell, from `C:\ai-excel`.

## 0. Before you start
* Close Excel windows that have a Touche/Gürsar workbook open. OneDrive may keep running.
* Write down the time; save all command output to `C:\ai-excel\loglar\deploy_<date>.txt` (`loglar\` is git-ignored):
  `Start-Transcript -Path "C:\ai-excel\loglar\deploy_$(Get-Date -f yyyyMMdd_HHmm).txt"`

## 1. Find and stop a stale instance
```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" } |
  Select-Object ProcessId, CommandLine
```
Stop it (`Stop-Process -Id <pid>`) only if the command line is `python … finans_asistani_sunucu.py`. Note which folder it ran from.

## 2. Inspect the local Git state (read-only)
```powershell
cd C:\ai-excel
git status
git branch -a
git remote -v
git log -n 20 --oneline --decorate
git fetch origin
git log --oneline HEAD..origin/main        # what you are missing
git log --oneline origin/main..HEAD        # local commits GitHub does not have
git diff --stat origin/main                # local differences
```
Expected `origin/main` after the merge: it contains the CFO/Smart Excel work (`git log origin/main --oneline | Select -First 12`).

## 3. Update to `main` without losing local work
| Situation | Action |
|---|---|
| Working tree clean, `origin/main..HEAD` empty | `git switch main` then `git merge --ff-only origin/main` |
| Working tree clean, you have local commits | `git branch local-preserve-$(Get-Date -f yyyyMMdd-HHmm)` (names them), then `git switch main; git merge --ff-only origin/main`. If `--ff-only` refuses, **STOP** and report the divergence |
| Uncommitted changes | `git switch -c local-preserve-$(Get-Date -f yyyyMMdd-HHmm)`; look at `git status --porcelain` (there must be **no** `.xlsx/.db/.env` — they are git-ignored); `git add -A`; `git commit -m "preserve local changes before deploy"`; then `git switch main; git merge --ff-only origin/main` |
After switching: `git diff main local-preserve-<ts> --stat` and read each file. Port only genuinely unique **source** work onto a new branch
(test it; push to `main` only if legitimate). Personal machine settings belong in `C:\ai-excel\.env` (ignored), never in source.

Verify: `(git rev-parse HEAD) -eq (git rev-parse origin/main)` must print `True`. Run `git status` — it must be clean.

## 4. Python environment
```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m pip install playwright ruff mypy       # tests / browser tests / checks
py -3.12 -m playwright install chromium            # once, for the browser tests
```

## 5. Identify the real MAIN / BACKUP (do not trust the file name)
Defaults (no configuration needed if your workbook is where it has always been):
MAIN = `%USERPROFILE%\OneDrive\Desktop\Touche\Touche Holding - AI Ready Finans Yönetim Sistemi.xlsx`,
BACKUP = the same folder, `…Sistemi_backup.xlsx`. Verify against the actual configuration:
```powershell
py -3.12 scripts\excel_operasyon.py durum
py -3.12 scripts\excel_operasyon.py dogrula
```
Record: MAIN path, size, modified time, SHA-256, sheet list (`dogrula`), active row count (`mutabakat` prints "Excel aktif satır").
**STOP** if `durum` says MAIN `var=False` — never create a blank workbook. If your old installation used another path (look in the
preserved branch's `scripts\config.py`), put it in `C:\ai-excel\.env`:
```
TOUCHE_MAIN_WORKBOOK=C:\full\path\to\the real workbook.xlsx
TOUCHE_BACKUP_WORKBOOK=C:\full\path\to\the real workbook_backup.xlsx
```
Re-run `durum` until MAIN is the file that holds your data. Do not rename the production workbook.

## 6. Test suite and static checks
```powershell
py -3.12 -m pytest -q                    # expect: 301 passed, 1 skipped (the 7 browser tests skip instead of pass if Chromium is not installed)
ruff check scripts tests --select F,E9   # All checks passed
py -3.12 -m mypy scripts/excel_deposu.py scripts/excel_operasyon.py scripts/excel_defter_mutabakati.py scripts/cfo_niyet.py scripts/cfo_sohbet.py scripts/cfo_sorgu.py scripts/cfo_varliklar.py scripts/cfo_yonlendirici.py scripts/proje_finansi.py scripts/excel_yazici.py scripts/windows_dogrulama.py scripts/cfo_eslestirme.py
```
The tests run in a temp directory and abort if the MAIN path points at your user folder. **STOP** if anything fails: fix the real cause, never weaken a financial test.

## 7. Windows / OneDrive validation (TEST workbooks only)
```powershell
py -3.12 scripts\windows_dogrulama.py --onedrive-klasoru "$env:USERPROFILE\OneDrive\Desktop\Touche"
```
It creates and removes its own `_SMART_EXCEL_TEST_<id>` folder, checks closed-Excel writes, V1→V2→V3 with BACKUP one version behind,
only MAIN+BACKUP on disk, no leftover temp, failure recovery, a **real Excel-style share lock** (write refused with `ANA_KILITLI`, MAIN
unchanged, BACKUP not advanced), retry after unlock, restore = swap, audit fields, and that your production files' hashes did not change.
All lines must be `PASS` (exit code 0). **STOP** on any `FAIL`.
(The lock check uses the same Win32 share mode Excel uses when it holds a workbook open, so no manual Excel step is needed.)

## 8. Inventory and reconciliation (read-only)
```powershell
py -3.12 scripts\excel_operasyon.py envanter       # report -> loglar\excel_envanter_<time>.md (git-ignored)
py -3.12 scripts\excel_operasyon.py mutabakat      # MAIN vs central ledger
```
The scan covers `C:\ai-excel`, the MAIN folder, the BACKUP folder and the old Gürsar folder, `.xlsx/.xlsm/.xls/.csv`, only files that look Smart
Excel related. For anywhere else you suspect (Downloads, Documents):
```powershell
py -3.12 scripts\excel_operasyon.py envanter --kok "$env:USERPROFILE\Downloads" --kok "$env:USERPROFILE\Documents"
Get-ChildItem $env:USERPROFILE -Recurse -Include *.xlsx,*.xlsm,*.xls -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'Touche|Gürsar|Gursar|Smart|AI Ready|_vade_|_backup|YEDEK|_once_' } | Select FullName, Length, LastWriteTime
```
Classes: `CANONICAL_MAIN`, `CANONICAL_BACKUP`, `SAFE_TO_DELETE`, `TEMP`, `LEGACY_BACKUP`, `OLD_MAIN`, `TEST`, `ARCHIVE`, `UNKNOWN`,
`UNIQUE_DATA_REVIEW_REQUIRED`. Only `SAFE_TO_DELETE` and `TEMP` are deletable; the report states why every other file is kept.
Do not decide from modification dates.

## 9. Unique / uncertain data — before deleting anything
For every file that is **not** `SAFE_TO_DELETE`/`TEMP`/MAIN/BACKUP:
```powershell
py -3.12 scripts\excel_operasyon.py kanit "<full path>"      # exit 0 = MAIN covers it; 3 = not proven
```
* `UNIQUE_DATA_REVIEW_REQUIRED` (records or work items MAIN does not have) → **STOP for that file**; list the missing IDs for the owner.
  There is deliberately no automatic old-workbook → MAIN merge. Records may be re-entered through the CFO Assistant or promoted with
  `entegre-et` only after the owner confirms them.
* `OLD_MAIN` (old Gürsar workbook, different layout), `LEGACY_BACKUP` (pattern matches, coverage unproven), `UNKNOWN` → keep, report why.
* Rows typed by hand into **MAIN** that the central ledger lacks (shown by `mutabakat`): after reading the list,
  `py -3.12 scripts\excel_operasyon.py defteri-aktar` (list only) then `… defteri-aktar --onay AKTAR` imports them into the ledger
  (idempotent, skips rows the app wrote, never touches the workbook). Re-run `mutabakat`; it must end with `TUTARLI`.
  A `Tutar farkı` line (amount edited in Excel after posting) is a **STOP**: the owner decides which amount is right.

## 10. Delete the proven-redundant copies
```powershell
py -3.12 scripts\excel_operasyon.py temizle                       # lists only
py -3.12 scripts\excel_operasyon.py temizle --sil --onay SIL      # deletes the SAFE_TO_DELETE / TEMP files listed
py -3.12 scripts\excel_operasyon.py temizle --dahil TEST          # lists TEST-named workbooks that ALSO pass the coverage proof
py -3.12 scripts\excel_operasyon.py temizle --dahil TEST --sil --onay SIL
```
Each deletion is written to the audit log (`excel_dosyasi_silindi`: path, SHA-256, class) and the now-empty generated folders
(`firmalar\*\yedek`, `firmalar\kurulum_yedek\*`, `arsiv\excel_yedek`) are removed. Folders that still contain anything stay. Nothing is moved
into another backup directory. (Files deleted inside a OneDrive folder go to the OneDrive recycle bin and can be restored from there.)
Re-run `envanter`; only canonical files, `UNIQUE…`/`OLD_MAIN`/`UNKNOWN`/`ARCHIVE` items you decided to keep, and nothing else may remain.

## 11. Create the single BACKUP
```powershell
py -3.12 scripts\excel_operasyon.py yedek-olustur       # copies MAIN to …_backup.xlsx (verified); refuses to overwrite a different existing BACKUP
py -3.12 scripts\excel_operasyon.py durum                # ANA = YEDEK: True, 0 leftover temp files
```
From now on every approved write moves MAIN V10→V11 and BACKUP V9→V10 automatically.

## 12. No other runtime write path
```powershell
git grep -n -E "\.save\(|os\.replace|shutil\.(copy|move)|copyfile" -- scripts
```
Only `excel_deposu.py` may save/replace Excel; the other hits are JSON state files, images or the log mover. (`TekYazmaHattiStatikTestleri` enforces this.)
Runtime temp files live in `C:\ai-excel\temp` (same volume as MAIN) and are removed after each write.

## 13. Acceptance on isolated TEST workbooks (not on production)
```powershell
py -3.12 -m pytest -q tests\test_cfo_sohbet.py tests\test_cfo_http.py tests\test_cfo_sorgu.py tests\test_proje_finansi.py tests\test_ui_tarayici.py
```
This runs, against a temporary workbook: the full sentence → draft (nothing written) → approval → exactly one record → the same sentence refused;
`10 bin ödeme yapıldı.` → Faaliyet → Proje → İş kalemi/Cari → Kime (amount never asked again); the five read queries with the workbook hash
unchanged; Holding → Gürsar Mimarlık → Göktürk Okulu in a real browser with back/forward/reload. **Do not post test transactions into the production workbook.**

## 14. Start production and verify
```powershell
py -3.12 scripts\finans_asistani_sunucu.py          # or start_finans_asistani.bat
```
The first lines must show: `SMART_EXCEL_ROOT=C:\ai-excel`, `SMART_EXCEL_MAIN_WORKBOOK=<your MAIN>`, `SMART_EXCEL_BACKUP_WORKBOOK=<…_backup.xlsx>`,
`SMART_EXCEL_TEMP_DIR=C:\ai-excel\temp`, `SMART_EXCEL_CODE=C:\ai-excel`. Open <http://127.0.0.1:8765/>:
Holding → click **Gürsar Mimarlık** (activity page; try back/forward/F5), Projeler → **Göktürk Okulu** (metrics, work items, counterparties,
recent transactions; unsupported numbers show `DATA_NOT_AVAILABLE`), and ask the five questions. Record `durum` before and after the queries —
the MAIN hash must be identical. Confirm one listener on 8765 whose command line is under `C:\ai-excel`.

## 15. Final scan and report
```powershell
py -3.12 scripts\excel_operasyon.py envanter
git status; git rev-parse HEAD; git rev-parse origin/main
Get-ChildItem "$env:USERPROFILE\OneDrive\Desktop\Touche" -Filter *.xlsx
```
The business folder must contain exactly MAIN and BACKUP (+ any file you consciously kept, listed with the reason).
Report separately: **active financial workbooks = 2**; source test fixtures/templates inside the repo are code, not data
(`tests\yardimci_excel.py` generates them in temp; no workbook is tracked in Git). Commit nothing but source: `.gitignore` already excludes
`*.xlsx`, `*.csv`, `*.db`, `*.log`, `.env`, `loglar/`, `temp/`, `veri/`, `arsiv/`, `firmalar/`.

## STOP conditions (report instead of acting)
MAIN not found · `git merge --ff-only` refused · tests/`windows_dogrulama` fail · `kanit`/`envanter` shows unique data in an old workbook ·
`mutabakat` shows a `Tutar farkı` · a file whose ownership by Smart Excel is unclear · BACKUP exists and differs from MAIN and nobody confirmed overwriting it.

---

## Prompt for Claude Code running locally in `C:\ai-excel`
> Execute `DEPLOY_WINDOWS.md` step by step in this folder, autonomously. Start with steps 1–3 (git inspection and a non-destructive update to
> `origin/main`; never reset/clean/force). Record every command and result. Use only the commands in the runbook for Excel deletion
> (`excel_operasyon.py temizle … --onay SIL`); never delete an Excel file any other way. Stop at any STOP condition and report it with the evidence.
> Finish with the final report: repository (HEAD == origin/main), active MAIN/BACKUP paths + hashes, cleanup counts (files and folders deleted,
> files kept and why), reconciliation result, Windows/OneDrive validation lines, acceptance results, the running process path and the startup banner,
> and the final scan of every remaining Smart Excel-related file.

## Kontrollü dağıtım kapısı

Dağıtım öncesi son main SHA ve post-merge CI sonucu [PRODUCT_READINESS_REPORT.md](PRODUCT_READINESS_REPORT.md)
ile karşılaştırılmalıdır. Ubuntu ve Windows CI yeşil değilse, sentetik mutabakat
PASS değilse veya tarayıcı kabul kanıtı yoksa işlem `NOT_FULLY_VERIFIED` olarak
kalır. Üretim dosyalarına geçişten önce sahip onayı, ayrı yedek anahtarı, geri
dönüş provası ve Windows/Excel kilit testi ayrıca yapılmalıdır.
