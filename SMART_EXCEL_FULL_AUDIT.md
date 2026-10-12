# SMART_EXCEL_FULL_AUDIT

Touche Finans AI — audit of the existing repository (base commit `8d44c2b`) and what was done about it.
The code was read, not the README. Evidence for every "Fixed" row is an automated test in `tests/`
(names given); "Open" rows are real remaining risks.

**Scope limit (read this first):** the audit ran in a cloud sandbox. The owner's production machine
(`C:\Users\…\Touche Holding - AI Ready Finans Yönetim Sistemi.xlsx`, `C:\ai-excel`, OneDrive) was **not
reachable**, so no production workbook was opened, hashed or modified, and no production Excel file was
deleted. The inventory/cleanup tooling was built and tested; it must be *run* on the owner's PC
(see `EXCEL_FILE_INVENTORY.md`).

Baseline (original commit, Linux): 6 test modules fail at import (`C:\ai-excel` log path, Telegram token at
import). With those two obstacles worked around: **126 passed, 21 failed** (`msvcrt` file lock), 1 skipped.
Now: **278 passed, 1 skipped**, ruff clean, mypy clean for all new/changed CFO and Excel modules.

Status legend: **Fixed** (code + test), **Mitigated** (made visible/safe, human action still needed), **Open**.

## CRITICAL

| ID | Where | Symptom → root cause | Financial impact | Fix | Test | Status |
|---|---|---|---|---|---|---|
| SX-C01 | `excel_yazici.py`, `faaliyet_yonetimi.py`, `finans_komutlari.py`, `proje_is_kalemleri.py`, `holding_ozeti_olustur.py`, `master_excel_entegre.py` | Six separate code paths each opened the master workbook, saved a temp file and `os.replace`d it with their own (different) backup/rollback logic; two scripts wrote the master directly. | Protection depended on which screen wrote; some paths had no formula/value guard. | One pipeline `excel_deposu.guvenli_degistir`; all writers rewritten onto it; the two bypass scripts deleted. | `TekYazmaHattiStatikTestleri` (only `excel_deposu` may save a workbook), `test_excel_deposu.py` | Fixed |
| SX-C02 | `excel_yazici._yedek_al`, `config.YEDEK_SAYISI`, `firmalar/*/yedek`, `firmalar/kurulum_yedek/<ts>`, `arsiv/excel_yedek`, `*_vade_<ts>.xlsx`, `Gursar_ONCE_HoldingOzeti_<ts>.xlsx` | Every write dropped a timestamped Excel copy (10 retained per folder, other folders unbounded). | "Which workbook is real?" confusion; stale copies can be edited/restored by mistake. | Exactly one BACKUP (`BACKUP_WORKBOOK_PATH`), refreshed and hash-verified before each write. Legacy copies are found/removed by `excel_operasyon.py envanter/temizle` (proof-gated). | `test_ilk_yazma_yedegi_olusturur_ve_tek_yedek_kalir`, `test_her_yazma_yedegi_yeniler_yeni_kopya_uretmez`, `test_tarihli_veya_islem_basina_yedek_mekanizmasi_yok`, `test_excel_operasyon.py` | Fixed in code; legacy files on the owner's PC: Mitigated (tool ready, not run) |
| SX-C03 | `finans_cekirdegi._hesap_eslesmesi` | Fallback `return "799","102"`: an unknown transaction type, or an **intercompany transfer**, was booked as an expense paid from bank. | Expenses overstated, profit understated, group transfer counted as cost. | Unknown/unmapped → `ValueError` (nothing written); intercompany points to the dedicated transfer flow. | `test_muhasebe_eslemesi.py` (`test_grup_ici_transfer_gidere_yazilamaz`, `test_bilinmeyen_tur_sessizce_gidere_yazilmaz`) | Fixed |
| SX-C04 | `excel_yazici.yaz(test_modu)` and rollback `shutil.copy2(backup, master)` | "Rollback" overwrote MAIN with a file copy; a restore path existed inside the normal writer. | A restore can destroy the newest approved records; reachable outside an ops action. | Restore = **swap** MAIN↔BACKUP, only in `excel_operasyon.py geri-don --onay GERI-DON`; AI/HTTP/Telegram layers cannot call it. | `test_geri_donus_ana_ile_yedegi_takas_eder`, `test_iki_kez_geri_donus_ilk_surume_doner`, `test_onay_olmadan_geri_donulmez`, `test_yapay_zeka_http_ve_telegram_katmani_geri_donusu_cagiramaz` | Fixed |
| SX-C05 | `excel_yazici._ilk_bos_satir`, `_mukerrer_*` | "First empty row" looked at **one** column; partially filled or formula rows could be written over; duplicate scans were cell-by-cell to row 500. | Overwritten rows, destroyed formulas. | A row is writable only if all identity columns are empty and none holds a formula; before/after snapshot diff rejects any changed existing value/formula; capacity guard. | `test_formul_hucresi_ezilemez`, `test_mevcut_veri_satiri_ezilemez`, `test_kismen_dolu_satirin_uzerine_yazilmaz`, `test_kapasite_dolunca_reddeder_ve_hicbir_sey_degismez`, `test_formul_enjeksiyonu_metin_olarak_yazilir` | Fixed |
| SX-C06 | `excel_yazici._mukerrer_kayit_mi` | Duplicate rule = same date+counterparty+amount: blocks **legitimate** repeated payments, yet a crash-and-retry still produced a second Excel row. | Wrongly blocked payments *and* real duplicates. | Deterministic `belge_id` → `Kaynak_Belge_ID`/`Kayit_ID`; a repeated approval is idempotent (ledger step still completes, no second row). A same-day identical *command* only warns, with explicit "Yine de ekle". | `test_ayni_belge_iki_kez_yazilmaz`, `test_ayni_belge_ayni_kayit_kimligini_uretir`, `test_ayni_taslak_iki_kez_onaylanamaz`, `test_birebir_ayni_komut_tekrar_yazilmaz_yine_de_ile_ikinci_kayit_olur` | Fixed |
| SX-C07 | Excel replaced, *then* SQLite ledger written | Failure in between left Excel and ledger different. | Totals disagree with the workbook. | Ledger step runs inside the pipeline (`ek_adim`); on failure MAIN is restored from BACKUP and the draft stays retryable. | `test_onay_sirasinda_excel_yazilamazsa_taslak_korunur_ve_tekrar_denenebilir`, `test_yerlestirme_hatasinda_ana_degismez_gecici_kalmaz` | Fixed |
| SX-C08 | OneDrive / open Excel | MAIN could change between read and replace; the stale snapshot then overwrote the user's edit. | Lost manual edits. | MAIN hash re-checked right before replace → `ANA_DEGISTI`, nothing written. Excel-open lock gives an actionable message. | `test_islem_sirasinda_disaridan_degisen_ana_ezilmez`, `test_excel_acikken_anlasilir_mesaj_verir` | Fixed |
| SX-C09 | openpyxl round-trip | Charts/images/pivots/embeddings can vanish on save. | Silent loss of workbook content. | Package-parts loss → write rejected (`PAKET_KAYBI`); charts openpyxl preserves still work. | `test_openpyxl_in_koruyamadigi_parca_varsa_yazma_reddedilir`, `test_grafik_openpyxl_ile_korunur_ve_yazma_calisir` | Fixed |
| SX-C10 | `config.py`, `main.py`, `kayit_kilidi.py`, tests | Hard-coded `C:\ai-excel`, user-desktop workbook path, `msvcrt`-only lock; no global test isolation. | Tests/tools could target the production path; not runnable elsewhere. | `TOUCHE_BASE_DIR` / `TOUCHE_MAIN_WORKBOOK` / `TOUCHE_BACKUP_WORKBOOK`; portable lock (fcntl/msvcrt); `tests/conftest.py` redirects everything to a temp dir; paths only in `config.py`. | whole suite; `test_ana_excel_yolu_yalniz_config_uzerinden_cozulur`, `test_kilit_ic_ice_cagrida_kilitlenmez` | Fixed |

## HIGH

| ID | Where | Symptom → root cause | Impact | Fix | Test | Status |
|---|---|---|---|---|---|---|
| SX-H01 | `finans_danismani.asistana_sor` | "Göktürk okulunda ne kadar ödeme yaptık?" ignored the project and answered with the **holding-wide** total (157.776 TL in the regression fixture). | Wrong answers presented as facts. | `cfo_sorgu`: structured query (metric + faaliyet/proje/cari + period) → read-only ledger calculation. | `test_gokturk_odemeleri_proje_kapsaminda_holding_toplami_degil` (also asserts the old path was wrong) | Fixed |
| SX-H02 | no module | No deterministic project/activity view; revenue, collection, expense, payment, profit and cash were not separated. | Profit confused with cash, receivable misread. | `proje_finansi`: every metric from the ledger with its formula; unavailable → `DATA_NOT_AVAILABLE`. | `test_gelir_tahsilat_gider_odeme_kar_nakit_ayri_metriklerdir`, `test_odeme_tahakkuksuzsa_uyarir_gider_sifir_gorunmesin`, `test_tahsilat_iptali_nakit_cikisi_odeme_sayilmaz`, `test_kredi_ve_tedarikci_iadesi_dogru_siniflanir` | Fixed |
| SX-H03 | chat → LLM draft | Entry depended on LLM parsing (slow, non-deterministic; chip clicks also went through it). Malformed output could garble fields. | Wrong/hallucinated fields reaching a draft. | Deterministic parser + structured draft + one-missing-field wizard; **no LLM call anywhere in the guided path**; complex instructions are deliberately left to the legacy (validated + approval-gated) path. | `test_hicbir_adimda_llm_cagrilmaz`, `test_eksik_komut_adim_adim_hicbir_alan_tekrar_sorulmaz`, `test_serbest_metin_cevaplar_ve_secenek_tiklamasi_ayni_sonucu_verir` | Fixed (legacy path: see SX-O02) |
| SX-H04 | activity naming | Excel says "Gürsar İnşaat", the owner says "Gürsar Mimarlık"; fixed names in code. | Wrong/unknown activity. | One registry from the workbook (`cfo_varliklar`) with aliases; writes use the workbook's own spelling, UI shows the owner's name. | `test_gursar_insaat_ve_mimarlik_ayni_faaliyet`, `test_faaliyet_projeden_cikarilir_ve_denetlenir` | Fixed |
| SX-H05 | entity resolution | No ambiguity handling. | Silent merge of two different people/projects. | `AMBIGUOUS` → selectable options; fuzzy matches are only *suggestions*; unknown person never auto-added. | `test_belirsiz_cari_secenekleri_adaylarla_sinirlanir`, `test_bilinmeyen_kisi_onay_olmadan_cariye_eklenmez`, `test_belirsiz_ad_tahmin_edilmez` | Fixed |
| SX-H06 | Excel vs ledger | Rows typed into Excel by hand never reached the ledger (metrics silently incomplete); `holding_excelini_aktar` was unwired and would **double count** app-written rows. | Understated totals, unseen divergence. | Read-only reconciliation (`excel_operasyon.py mutabakat`), holding page warning, safe explicit import (`defteri-aktar --onay AKTAR`, skips app-written rows, never touches Excel). | `test_excel_mutabakati.py` (10 tests) | Mitigated (a human must run the import for old manual rows) |
| SX-H07 | `ui_belge_servisi` approval | Approved drafts could be re-approved / revalidated against stale data. | Duplicate or stale writes. | Wizard drafts re-validated against current master data at approval; state machine prevents double approval. | `test_ayni_taslak_iki_kez_onaylanamaz`, `test_onay_tek_kayit_yazar_denetim_orijinal_talimati_tasir` | Fixed |
| SX-H08 | RBAC on new surface | Scope must hold for the new endpoints. | Cross-activity data exposure. | `kapsam_izinli` enforced on holding/activity/project endpoints; wizard options limited to the user's scope; other users cannot touch your draft. | `test_http_kapsamli_kullanici_baska_faaliyeti_goremez_secemez`, `test_http_baska_kullanici_taslagini_degistiremez`, `test_http_viewer_taslak_olusturamaz` | Fixed |
| SX-H09 | queries | A question must never mutate. | Query turning into a write. | Read connection uses `PRAGMA query_only=ON`; Excel never opened by queries; no draft is created. | `test_sorgular_excel_ve_defteri_degistirmez`, `test_okuma_baglantisi_yazmayi_motor_duzeyinde_engeller`, `test_http_sorgu_okuma_yapar_excel_degismez` | Fixed |

## MEDIUM

| ID | Where | Finding | Fix / status |
|---|---|---|---|
| SX-M01 | `proje_is_kalemleri` | Cell-address access on a read-only sheet is O(n²) (0.4 s per read). | Single `iter_rows` pass (0.027 s). Fixed |
| SX-M02 | `master_data` | Workbook re-read on every call. | Cache keyed by path+mtime+size (`onbellegi_temizle`). Fixed |
| SX-M03 | `main.py` | Import crashed when the log folder was missing. | `_log_kur()` creates it / falls back to console. Fixed |
| SX-M04 | `faaliyet_yonetimi` | Fixed activity name fallback `'Gürsar İnşaat'`; per-creation backup folder. | Name comes from master data; single pipeline. Fixed. Remaining hard-coded system accounts/keys (`GURSAR_SISTEM_*`, `FIRMA_ANAHTARLARI`) — **Open** |
| SX-M05 | `ui/cfo.js` | Deep link (`#/proje/...`) rendered an **empty page** on first load (render ran before views were registered). | Fixed; browser test reloads a deep link |
| SX-M06 | `ui/app.js` | Chat form listeners were attached only after awaited data loads; an early "Gönder" reloaded the page. | Listeners bound first. Fixed (found by the browser test) |
| SX-M07 | `ui/cfo.js` | Loading skeleton stayed in the DOM; a slow earlier render could overwrite a newer page. | Render into a detached box, swap only if still current. Fixed |
| SX-M08 | `ui/cfo.js`, `index.html` | Inline `style` attributes violated the server's `style-src 'self'` CSP (console errors). | Moved to CSS classes; browser tests fail on any console error. Fixed |
| SX-M09 | error UX | Any `PermissionError` was reported as "Excel is locked". | Only the final MAIN replace maps to the lock message; other errors keep their typed code (`DepoHatasi.kod`). Fixed |
| SX-M10 | `finans_cekirdegi.veritabani_yedekle` | Timestamped **SQLite** backups in `arsiv/veri_yedek` (30 kept). Not Excel, bounded. | Accepted; documented |
| SX-M11 | ledger template | `03_Kayıt Merkezi` formulas are prepared to row 300 (`TOUCHE_LEDGER_SON_SATIR`); the writer refuses beyond it (`KAPASITE_DOLU`, nothing written). | Safe by design but the owner must extend the template in Excel when near the limit. **Open** (operational) |

## LOW

| ID | Finding | Status |
|---|---|---|
| SX-L01 | Unused imports/locals (ruff F), undefined name in new code | Fixed; `ruff check scripts tests --select F,E9` clean |
| SX-L02 | A literal `C:\ai-excel` folder was created in the repo on non-Windows by the old config | Fixed (portable base dir). The stray sandbox folder (empty schema-only DB, 0 journal entries) was removed; it was git-ignored |
| SX-L03 | `mypy` on legacy modules: 175 errors (`telegram_bot.py` 110, `finans_asistani_sunucu.py` 14, …) | Open (pre-existing, untouched) |
| SX-L04 | `start_finans_asistani.bat` hard-codes `C:\ai-excel` | Left (it is the Windows production launcher) |

## Open operational risks (not code bugs)

- **SX-O01** Production workbook and legacy copies were not inspected (sandbox). Run `excel_operasyon.py envanter` first.
- **SX-O02** Complex instructions (several amounts, future/vade, çek/kredi rules) intentionally stay on the legacy LLM path. It is validator- and approval-gated, but it is the one place a model still proposes field values.
- **SX-O03** Excel rows typed by hand reach metrics only after `defteri-aktar`.

## Scorecard (0–10)

"Before" = my assessment of the base commit from reading its code and running its tests; "After" is tied to the
evidence in this repository. Nothing is a 10: the production workbook, Windows file locks, OneDrive and a real LLM were
not available here.

| Category | Before | After | Evidence / what holds it back |
|---|---:|---:|---|
| Financial data integrity | 3 | 8 | single guarded pipeline, rollback, no silent expense booking (tests above). Held back: production data never exercised; legacy LLM path remains |
| Excel reliability | 4 | 8 | atomic replace, validation, external-change detection. Windows lock/OneDrive simulated only |
| Backup simplicity | 2 | 8 | exactly one BACKUP, static + behavioural tests. Legacy copies on the owner's PC not yet removed (tool ready) |
| CFO understanding | 4 | 8 | grammar covered by tests incl. the brief's sentences; complex sentences deliberately go to the legacy path |
| Guided transaction entry | 3 | 8 | wizard unit, HTTP and real-browser tests; date/currency defaults are shown, not asked |
| Activity/project resolution | 4 | 8 | registry from the workbook, ambiguity never guessed |
| Holding navigation | 4 | 9 | clickable Holding→Activity→Project, back button, deep link, no console errors (Chromium) |
| Project detail quality | 2 | 8 | all requested metrics or `DATA_NOT_AVAILABLE`; fixtures, not real data |
| Project financial calculations | 3 | 8 | revenue≠collection, expense≠payment, profit≠cash proven on hand-computed fixtures |
| Query accuracy | 4 | 8 | scoped, read-only, regression-tested against the old wrong answer |
| Duplicate protection | 4 | 8 | deterministic IDs + idempotent approval; legitimate repeats allowed |
| Auditability | 5 | 8 | operation/transaction ids, original sentence, hashes, status; "why is this here" query |
| UX simplicity | 4 | 7 | one question at a time, chips; no user study, desktop viewport only |
| Performance | 4 | 8 | measured on a 40-entry ledger: chat→draft ≈10 ms, chip click ≈13 ms, project page ≈19 ms (median, no model call); the old path needed an LLM call (timeout 180 s) — not measurable without a model |
| Maintainability | 4 | 7 | one writer, central config, typed new modules; legacy modules still large, 175 mypy errors there, some hard-coded entities remain |
| Test coverage | 5 | 8 | 147 → 279 tests (incl. 7 browser tests), baseline could not even be collected on Linux; Windows-specific and LLM paths untested |
