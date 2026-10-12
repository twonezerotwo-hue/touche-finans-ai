# ARCHITECTURE

Touche Holding **Smart Excel + CFO Assistant**. Python 3.12 (stdlib `http.server`), vanilla-JS UI, openpyxl,
SQLite ledger. Windows 11 is the production target; everything also runs on Linux (tests, CI).

```
USER ─► CFO ASSISTANT (chat / wizard chips)
          │  cfo_yonlendirici ── routes: query | record | wizard answer | correction | legacy
          ▼
   STRUCTURED INTENT  (cfo_niyet: Niyet)           ◄── entity registry (cfo_varliklar, from the workbook)
          │
          ▼
   VALIDATION  (dogrulayici.deterministik_dogrula, master data)
          │
          ▼
   FINANCIAL DOMAIN  (finans_cekirdegi: double-entry mapping; proje_finansi: metrics)
          │   draft ──► password approval (ui_belge_servisi, the ONLY approval path)
          ▼
   CONTROLLED WRITER  (excel_yazici → excel_deposu.guvenli_degistir)
          │                                   └─► SQLite ledger step inside the same transaction
          ▼
   AUDIT (loglar/audit.jsonl, hash-chained)  ──►  UI refresh
```

*AI understands; code validates; the financial domain decides; one controlled writer modifies Excel.*
The LLM is **not** in the main path any more: the parser, the wizard, the queries and the metrics are
deterministic. Only complex instructions (several amounts, future/vade, çek/kredi rules) still go to the legacy
LLM-assisted path, which is validator- and approval-gated.

## 1. Files at runtime

| File | Role | Path |
|---|---|---|
| **MAIN** workbook | the only live Smart Excel | `config.MAIN_WORKBOOK_PATH` (`TOUCHE_MAIN_WORKBOOK`; Windows default = the existing OneDrive workbook, unchanged) |
| **BACKUP** workbook | the previous version, refreshed before every approved write | `config.BACKUP_WORKBOOK_PATH` (`TOUCHE_BACKUP_WORKBOOK`; default: next to MAIN as `<main stem>_backup.xlsx`) |
| audit history | one JSONL line per operation, hash-chained | `loglar/audit.jsonl` |
| central ledger | double-entry mirror used for all metrics | `veri/finans.db` (SQLite) |
| wizard state / approval queue | drafts | `loglar/cfo_taslaklari.json`, `loglar/ui_belge_kuyrugu.json` |
| text index cache | JSON derived from MAIN by hash (not a workbook) | `veri/excel_index/` |

There are no dated, per-transaction or folder backups, and no second source workbook. Temporary files
(`.<name>.<32 hex>.tmp.xlsx`) exist only inside a write, in the project temp area (`<base>/temp`, so nothing is dropped into
the OneDrive folder; if temp is not on the same volume as the target — where `os.replace` could not be atomic — the
target's own folder is used) and are removed afterwards. Leftovers are removed at the next write; only files matching that
exact pattern in temp or next to MAIN/BACKUP are ever deleted. The server prints `SMART_EXCEL_ROOT`,
`SMART_EXCEL_MAIN_WORKBOOK`, `SMART_EXCEL_BACKUP_WORKBOOK`, `SMART_EXCEL_TEMP_DIR`, `SMART_EXCEL_CODE` at startup.

Environment variables: `TOUCHE_BASE_DIR`, `TOUCHE_MAIN_WORKBOOK`, `TOUCHE_BACKUP_WORKBOOK`,
`TOUCHE_LEDGER_SON_SATIR` (template capacity, default 300), `TOUCHE_PAKET_KAYBI_IZNI=1` (explicitly allow dropping
unsupported workbook parts — off by default), plus the existing `FINANS_UI_PORT`, Ollama/EVREN and Telegram settings.
All workbook paths are resolved only through `config.ana_excel_yolu()` / `config.yedek_excel_yolu()`.

## 2. The single write pipeline — `scripts/excel_deposu.py`

`guvenli_degistir(degistirici, …)` is the **only** code that saves MAIN (enforced by a static test). Under a
re-entrant cross-process lock (`kayit_kilidi`: `fcntl` / `msvcrt`):

1. MAIN exists and is a valid XLSX with the expected sheets — otherwise nothing changes.
2. The change is applied **in memory**; a rejected change (`basari=False`) leaves MAIN and BACKUP untouched.
3. Saved to a temp file next to MAIN, re-opened and validated (sheets, formulas, existing values via a before/after
   snapshot diff, lost chart/image/pivot parts).
4. **Before BACKUP is touched**: MAIN must be writable (an open Excel/OneDrive lock → `ANA_KILITLI`, actionable "close Excel"
   message) and its hash must still be the one read in step 1 (`ANA_DEGISTI`). A refused attempt therefore never moves BACKUP,
   so "undo the last transaction" (MAIN = V3, BACKUP = V2) stays possible.
   Then BACKUP := current MAIN, copied to a temp file, hash-verified, atomically placed.
5. MAIN hash re-checked once more after the backup copy (race with Excel/OneDrive) → abort (`ANA_DEGISTI`).
6. Atomic `os.replace(temp, MAIN)`; a lock that appears only now still maps to the "Excel is open" message.
7. Placement verified by hash; then the extra step (the SQLite ledger) runs; if it fails MAIN is restored from BACKUP
   and the error is re-raised (draft stays retryable).
8. Temp files removed; one audit event written (also for rejections and errors).

Result: `MAIN = V11`, `BACKUP = V10`. Existing formulas and filled cells cannot change; a row is writable only when
all identity columns are empty and no target cell holds a formula; formula-looking text is stored as text.

**Restore** = swap MAIN↔BACKUP (`yedekten_geri_don`), requires the word `GERI-DON`, actor and reason, and is reachable
**only** from `scripts/excel_operasyon.py geri-don`. The HTTP server, Telegram bot, UI and AI modules never call it
(static test). If the very last restore step fails, the previous MAIN is kept as `KURTARMA_<backup name>.xlsx` (a name the temp cleanup never matches) and the error says where.
`harici_dosyayi_ana_yap` places an externally edited, validated workbook as the new MAIN (BACKUP = previous MAIN).

### Audit event (`excel_islem`)
`islem_id` (operation id), `aktor`, `kaynak`, `durum` (`BASARILI`, `REDDEDILDI`, `HATA_GERI_ALINDI`, …), `hata`/`hata_kodu`,
`ana_hash_once`, `ana_hash_sonra`, `yedek_hash`, `sure_ms`, and — for ledger writes — `belge_id`, `taslak_id`,
`orijinal_talimat` (the user's sentence), `normalize_komut`, `onay_kaynagi`, and per record `transaction_id`
(`Kayit_ID`), faaliyet, proje, işlem türü, cari, hesap, tutar, PB, tarih, açıklama. `cfo_sorgu` logs read queries
(question hash, metric, scope). "Bu ödeme neden burada?" is answered from this chain by `cfo_sorgu.kayit_gecmisi`.

## 3. CFO layer

| Module | Responsibility |
|---|---|
| `cfo_varliklar` | Entity registry (activities, projects, counterparties, accounts, work items) loaded from the workbook. Turkish-safe normalization (`norm`), suffix-tolerant token matching ("okuluna"≈"okulu"), alias handling ("Gürsar İnşaat"="Gürsar Mimarlık"), `TEKIL / BELIRSIZ / ONERI / YOK` resolution. Fuzzy matches are suggestions only. |
| `cfo_niyet` | Deterministic sentence → `Niyet`: transaction type, amount (`10 bin`, `10k`, `10.000`, `1,5 milyon`), currency, date, entities. Classifies query vs record; flags "complex" and unsupported (transfer/correction/reversal) instructions. |
| `cfo_sohbet` | Structured draft state (`known / missing / ambiguous`), one-question-at-a-time wizard (Faaliyet → Proje → İşlem → İş kalemi/Cari → Kime → Tutar → PB → Tarih → Hesap), history-based account inference (shown with its reason), corrections, repeat guard, persistence (atomic JSON), hand-off to the approval queue. No LLM call. |
| `cfo_sorgu` | Natural-language question → structured query → read-only ledger calculation → templated answer with scope, period, source and data date. |
| `proje_finansi` | Holding / activity / project metrics (see §4). |
| `cfo_yonlendirici` | Routing for `/api/chat`, `/api/cfo/adim`, and the read endpoints. |

Approval reuses the existing queue and password flow (`ui_belge_servisi`); wizard drafts are re-validated against the
current master data at approval time.

Idempotency: `belge_id` is derived from the draft id, `Kayit_ID` from `belge_id` (SHA-256), `Kaynak_Belge_ID` is unique;
a repeated approval writes nothing new and still completes the ledger step. An identical command on the same day only
produces a warning with explicit "Yine de ekle / Vazgeç" — legitimate repeated payments are never blocked.

## 4. Financial semantics (`proje_finansi`, chart of accounts)

| Metric | Definition (never mixed) |
|---|---|
| Kazanılan gelir | net credit on income accounts 600/646/649 — **not** collection |
| Gider (maliyet) | net debit on 740/770/780/799 — **not** payment |
| Tahsil edilen | cash received closing receivables 120/131/159 |
| Ödenen | cash leaving 100/101/102 |
| Kalan alacak | net debit balance of 120/131/159 |
| Açık borç | net credit balance of 300/320/331/335/336 (negative ⇒ payment without accrual → warning) |
| Kâr / zarar | gelir − gider (result, **not** cash) |
| Net nakit | tahsil edilen + direct cash income − ödenen (**not** profit) |

Payment = Dr 320 / Cr cash; accrual = Dr 740 / Cr 320; collection = Dr cash / Cr 120; hakediş = Dr 120 / Cr 600; reversal
entries net out the original. Intercompany (131/331, paired entries) is never expense/payment/collection and is
eliminated in the Holding view. A metric that cannot be derived returns `DATA_NOT_AVAILABLE` (no invented number).
Holding totals are summed over the activities that actually exist in the workbook (nothing is Gürsar-specific).
The read connection is `PRAGMA query_only=ON`.

**Excel vs ledger.** Metrics come from the ledger. `excel_defter_mutabakati` (read-only) lists Excel rows missing from
the ledger, amount differences and orphan ledger entries; the Holding page shows a warning when they differ.

## 5. UI

Hash routes: `#/holding`, `#/aktivite`, `#/faaliyet/<key>`, `#/proje` , `#/proje/<key>/<project>`, `#/islemler`; the
CFO Assistant is the main panel. Holding → click an activity → activity detail (metrics, projects, counterparty
balances, recent transactions) → click a project → project detail (metrics, work items, counterparties, transactions,
"Ödeme/Gider/Tahsilat ekle" which opens the wizard with the context filled). Wizard options are buttons built from real
data; clicking a button calls `/api/cfo/adim` (no LLM). Browser tests assert that nothing is logged to the console.

## 6. Operations tool — `scripts/excel_operasyon.py`
`durum`, `dogrula`, `mutabakat`, `defteri-aktar`, `envanter`, `temizle`, `geri-don`, `entegre-et` — human/ops only, never
imported by the server, bot or AI layer.
