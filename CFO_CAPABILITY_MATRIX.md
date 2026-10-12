# CFO_CAPABILITY_MATRIX

What the CFO Assistant can do **deterministically** (no model call), what it hands to the legacy path, and what it
refuses. "Tested" names point to `tests/`.

## 1. Recording — natural language → draft → password approval → Excel

| Intent (`cfo_niyet`) | Workbook `Islem_Turu` (only if defined in `99_Ayarlar`) | Ledger posting | Asked when missing |
|---|---|---|---|
| `COUNTERPARTY_PAYMENT` / `PAYMENT` / `WORK_ITEM_PAYMENT` | Usta/Tedarikçi Ödemesi (`Ortak Cari Ödemesi` for partner payments) | Dr 320 payable / Cr cash 100·102 (or Dr 331 for partner) — **settles a payable, is not an expense** | faaliyet, proje, "iş kalemine mi / cari ödemesi mi?", kalem or cari, **hesap** |
| `EXPENSE` / `PROJECT_COST` / `PAYABLE` | Gider Tahakkuku | Dr 740 / Cr 320 — cost + liability, **no cash** | faaliyet, proje, iş kalemi, cari (no hesap) |
| `COLLECTION` | Tahsilat | Dr cash / Cr 120 — **closes a receivable, is not revenue** | faaliyet, proje, kimden, **hesap** |
| `RECEIVABLE` / `PROJECT_REVENUE` | Hakediş / Gelir Tahakkuku | Dr 120 / Cr 600 — revenue earned, **not collected** | faaliyet, proje, müşteri (no hesap) |
| `INCOME` | Diğer Gelir | Dr cash / Cr 649 | faaliyet, proje, hesap |
| `TRANSFER` | — | not done in chat | explains: use the "Grup içi transfer" screen (paired source/target entries, eliminated in Holding) |
| `CORRECTION`, `REVERSAL` | — | not done in chat | explains: edit an open draft with `Düzelt: …`, or cancel a posted document from the document screen with the password |
| `QUERY` | — | read-only (see §2) | — |

If the workbook does not define the matching `Islem_Turu`, the wizard asks the user to choose among the defined types
instead of inventing one. An unmapped type can never post as an expense (`test_bilinmeyen_tur_sessizce_gidere_yazilmaz`).

**Field order of the wizard**: Faaliyet → Proje → İşlem → İş kalemi / Cari → Kime → Tutar → Para birimi → Tarih → Hesap.
Only the missing fields are asked, one at a time, with real options (activities from the workbook, projects of the
chosen activity, accounts of that activity in the right currency, the project's work items, known counterparties plus
"Listede yok — yeni kişi/firma ekle"). Typing the answer works exactly like clicking it. A project that belongs to one
activity implies the activity (recorded as a note in the draft/audit).

**Defaults that are shown, never hidden**: date = today when not stated ("Tarih belirtilmediği için bugün varsayıldı");
currency = explicit text > the chosen account's currency > TL; account = filled automatically only when the activity has
a single suitable account or the same counterparty+type always used one account (the reason is printed). Amount, type and
counterparty are never guessed from history. Everything can be changed with `Düzelt: tutar 12 bin, hesap …`.

### Tested sentences
`Göktürk okuluna Gürsar Mimarlık adına Devrim'e 10 bin TL ödeme yapıldı.`
`Göktürk okulunda Devrim'e Gürsar'dan 10.000 ödeme yaptık` · `Göktürk Okulu Devrim 10 bin ödeme` ·
`Gürsar Mimarlık Göktürk projesi için Devrim'e bugün 10k TL ödendi.` · `göktürk okulu / devrim / 10000 TL ödeme yapıldı`
(all → Gürsar Mimarlık / Göktürk Okulu / Devrim / 10.000 TL) · `Göktürk okulunda elektrik iş kalemine 25 bin TL gider yaz`
(→ Gider Tahakkuku on work item Elektrik) · `10 bin ödeme yapıldı.` and `5 bin gider ekle.` (guided) ·
`yeni ödeme`, `yeni gider`, `kayıt ekle` (empty start, everything asked) · amounts `10 bin`, `10k`, `10.000`, `10.000TL`,
`12.500,50 TL`, `1,5 milyon TL` · dates `05.10.2026`, `dün`, `5 Ekim`, invalid `31.02.2026` is reported, not guessed.

### Safeguards
* Ambiguous names (two "Devrim", two "Göktürk" projects) → options, never a silent pick; typos are suggestions only.
* A person who is not in the workbook is **not** added silently; creating one needs an explicit choice and approval.
* A draft is owned by its creator; other users cannot answer or change it. VIEWER role cannot create drafts.
* Nothing is written before the password approval; approval re-validates against current master data.
* Same draft approved twice → one record. Same command again the same day → warning with "Yine de ekle / Vazgeç".
* Any failure keeps the draft (retryable) and says in plain language that nothing was recorded.

### Handed to the legacy LLM-assisted path (still validator- and approval-gated)
several different amounts in one sentence, future/vade wording ("ödenecek", "haftaya"), çek / kredi / teminat / faiz
rules, very long or multi-line instructions (`test_karmasik_talimatlar_mevcut_llm_hattina_birakilir`).

## 2. Questions — natural language → structured query → deterministic result

Metrics: **ödeme, tahsilat, kazanılan gelir, kalan alacak, açık borç, gider (tahakkuk), kâr/zarar, net nakit**, plus
"kalem kalem göster" detail lists and "<kayıt no> neden burada?" (history from the audit chain).
Scope: Holding (consolidated, intercompany eliminated) / activity / project / counterparty / period (`bu ay`, `Eylül`,
all time). Every answer states scope, period, source and data date; "ödeme ≠ gider", "kazanç ≠ tahsilat" and
"kâr ≠ nakit" are called out where relevant. Read-only: the Excel hash, backup and ledger row counts are asserted
unchanged (`test_sorgular_excel_ve_defteri_degistirmez`). Unsupported questions return nothing and fall through to the
existing advisor; ambiguous names ask which one was meant.

Examples (all tested): `Göktürk okulunda ne kadar ödeme yaptık?` · `Göktürk okulundan ne kadar kazandık?` ·
`Göktürk okulunda ne kadar alacağımız kaldı?` · `Gürsar'ın toplam gideri ne?` · `Devrim'e toplam ne kadar ödeme yapıldı?` ·
`Bu ay Holding toplam ne kadar ödeme yaptı?` · `Göktürk okulunun kârı ne kadar?`

## 3. Pages and data endpoints (read-only, scope-checked)

| UI | Endpoint | Content |
|---|---|---|
| Holding | `GET /api/holding-ozeti` | consolidated metrics, activities (clickable), recent transactions, warnings (incl. Excel↔ledger differences) |
| Activity detail | `GET /api/faaliyet-detay?faaliyet=` | metrics, projects (clickable), counterparty balances, recent transactions |
| Projects / project detail | `GET /api/proje-listesi`, `GET /api/proje-detay?faaliyet=&proje=` | contract value, earned revenue, collected, paid, cost, remaining receivable, open payable, profit, margin, net cash, work items (planned / realised / paid / remaining), counterparty balances, recent transactions, definitions |
| Wizard | `POST /api/chat`, `POST /api/cfo/adim`, `GET /api/cfo/taslak` | draft state, next question with options |

A metric that cannot be derived is shown as `DATA_NOT_AVAILABLE` (e.g. margin when revenue is zero). Contract value is
the sum of **approved** work items only (drafts excluded).

## 4. Not supported / known limits
* Transfers, corrections and reversals from chat (explained, with the right screen).
* Splitting one payment across several work items or counterparties in one sentence.
* Natural-language document classification beyond the existing upload pipeline (unchanged).
* Metrics for Excel rows typed by hand appear only after `excel_operasyon.py defteri-aktar` (the Holding page warns).
* Currency defaults to TL when neither the sentence nor the chosen account says otherwise (always visible in the draft).
