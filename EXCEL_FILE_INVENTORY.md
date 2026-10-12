# EXCEL_FILE_INVENTORY

## What could and could not be inventoried

This work was done in a cloud sandbox that has **only the repository** (no `C:\Users\…`, no OneDrive, no
`C:\ai-excel`). A search of the sandbox found **zero** `.xlsx/.xlsm/.xls` files outside the temporary workbooks the
tests create themselves, and the repository tracks none (`.gitignore` excludes them).

Therefore **no production workbook was inspected, hashed, opened, modified or deleted**, and no real stale copy was
compared against MAIN. A list of "files on your computer" written here would be invented, so none is given.
The tooling to produce the real inventory was built and tested instead; run it on the machine that hosts the app.

## Runtime contract (after this change)

| Role | Setting | Default on Windows |
|---|---|---|
| MAIN | `TOUCHE_MAIN_WORKBOOK` → `config.MAIN_WORKBOOK_PATH` | the existing `…\Desktop\Touche\Touche Holding - AI Ready Finans Yönetim Sistemi.xlsx` (name unchanged) |
| BACKUP | `TOUCHE_BACKUP_WORKBOOK` → `config.BACKUP_WORKBOOK_PATH` | next to MAIN: `…\Desktop\Touche\Touche Holding - AI Ready Finans Yönetim Sistemi_backup.xlsx` |

Nothing else at runtime reads or writes an Excel workbook (`TekYazmaHattiStatikTestleri`). The old Gürsar workbook
path (`config.LEGACY_GURSAR_EXCEL`) is referenced only by the inventory tool and is never opened at runtime.

## Copies the *old* code generated (what to look for)

| Pattern (relative to `C:\ai-excel` unless noted) | Created by | Classified as |
|---|---|---|
| `firmalar\<faaliyet>\yedek\*.xlsx` | `excel_yazici._yedek_al` (10 kept per folder) | old generated copy |
| `firmalar\kurulum_yedek\<timestamp>\*.xlsx` | `faaliyet_yonetimi._toplu_kaydet` (never pruned) | old generated copy |
| `arsiv\excel_yedek\*.xlsx` | `master_excel_entegre` | old generated copy |
| `*_vade_<timestamp>.xlsx` (next to MAIN / backup folder) | `finans_komutlari.acik_borc_vadesi_guncelle` | old generated copy |
| `Gursar_ONCE_HoldingOzeti_<timestamp>.xlsx` | `holding_ozeti_olustur.py` (removed) | old generated copy |
| `*_hesap.xlsx` | old per-activity workbooks | old generated copy |
| `.*.tmp.xlsx` | interrupted writes | leftover temp |

## How to produce the real inventory and clean up (on the owner's PC)

```powershell
cd C:\ai-excel
py -3.12 scripts\excel_operasyon.py durum         # MAIN/BACKUP path, size, SHA-256, leftover temp files
py -3.12 scripts\excel_operasyon.py dogrula       # both files open and have the expected sheets
py -3.12 scripts\excel_operasyon.py envanter      # scans BASE_DIR, the MAIN/BACKUP folders and the legacy Gürsar folder
                                                  #  -> writes EXCEL_FILE_INVENTORY.md (replace this page with the output)
py -3.12 scripts\excel_operasyon.py mutabakat     # MAIN vs central ledger (read-only)
py -3.12 scripts\excel_operasyon.py temizle       # LISTS provably redundant generated copies, deletes nothing
py -3.12 scripts\excel_operasyon.py temizle --sil --onay SIL   # only after you have read the list
```

`envanter` records, for each Touche-related workbook or CSV (path, size, modified time, SHA-256, sheets, role, **class**) and decides.
Classes: `CANONICAL_MAIN`, `CANONICAL_BACKUP`, `SAFE_TO_DELETE`, `TEMP`, `LEGACY_BACKUP` (pattern matches but coverage could not be
proven → human decision), `OLD_MAIN`, `TEST`, `ARCHIVE`, `UNKNOWN`, `UNIQUE_DATA_REVIEW_REQUIRED`. Only `SAFE_TO_DELETE` and `TEMP` are deletable.
Coverage proof for any non-identical file: readable, ledger sheet present, no filled sheet MAIN lacks, no `Kayit_ID` / `Kalem_ID` MAIN lacks.

| Role | Meaning | Deletable? |
|---|---|---|
| `MAIN` / `BACKUP` | the two runtime files | never |
| `KOPYA(ANA ile birebir ayni)` | byte-identical to MAIN **and** matches a generated-copy pattern | yes |
| `ESKI_URETILMIS_KOPYA` | matches a generated-copy pattern **and** has no `Kayit_ID` that MAIN lacks | yes |
| `TEKIL_KAYIT_VAR(silinmez)` | contains records MAIN does not have | **never** — reconcile by hand first |
| `OKUNAMADI/ESKI_FORMAT(silinmez)` | `.xls`, corrupt or unreadable | **never** |
| `KAPSAMA_KANITLANAMADI(silinmez)` | no ledger sheet, extra filled sheet, or unknown work-item id — cannot prove MAIN covers it | **never** (reason is printed in the report) |
| `CSV(silinmez)` | listed only | **never** |
| `BELIRSIZ(silinmez)` | related by name but not a known generated pattern | **never** |

Only files whose **name** contains Touche/Gürsar/Smart Excel markers, or that live in the system's own `firmalar\` /
`arsiv\` folders, are considered at all; other Excel files on the computer (and a parent folder name alone) are
ignored. Deletion requires both `--sil` and `--onay SIL`. If in doubt, the tool keeps the file and says so —
information preservation wins over a file count.

If `envanter` shows a `TEKIL_KAYIT_VAR` file, compare it with MAIN row by row before doing anything; the tool will not
merge workbooks. `entegre-et <file>` can promote a reviewed workbook to MAIN (the previous MAIN becomes BACKUP).

## Verification already done (sandbox, test workbooks only)

* After many consecutive writes, the test folder contains exactly the MAIN and BACKUP workbooks, no `*.tmp*`
  (`test_her_yazma_yedegi_yeniler_yeni_kopya_uretmez`, `test_artik_gecici_dosya_sonraki_islemde_silinir_baska_dosyaya_dokunulmaz`).
* `test_excel_operasyon.py` builds a fake tree (MAIN, BACKUP, identical copy, old install backup, a copy holding a unique
  record, an unrelated workbook) and shows that only the provably redundant generated copies are deletable, deletion
  needs the `SIL` word, and the unrelated workbook is never touched.
* A stray empty folder literally named `C:\ai-excel` that the *old* config had created inside the sandbox checkout
  (schema-only SQLite file: 0 journal entries; a lock file) was removed; it was git-ignored and never contained financial data.

## Production hashes

Production MAIN SHA-256 before/after this work: **not applicable** — the file was never available to this
environment and no test can reach it (`tests/conftest.py` aborts the session if the MAIN path leaves the temp
directory). Please record `excel_operasyon.py durum` output before and after you deploy.
