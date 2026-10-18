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

### Everyday Turkish (`cfo_dil.py`, deterministic — no model call)
Understood in addition to the sentences above (all in `tests/test_cfo_anlama.py`, `tests/test_cfo_dil.py`):
* **Verbs** — `ödedik/verdim/gönderdim/yatırdım/havale yaptık/elden verdik/avans verdik`, `… ödendi/verildi/ödenmiş`.
  Generic verbs (`verdik`, `gönderdik`, `yatırdık`) count as a payment only with a *strong* amount (TL/bin/thousand separator):
  `Müdüre 3 rapor verdik` is not a payment.
* **Direction** — a customer/işveren *paying* is a collection: `İşveren 100 bin ödedi`, `İşverenden 100 bin aldık`, `hesabımıza yatırdı`,
  `EFT geldi`; money going *to* the customer (`İşverene iade ödedik`) stays a payment. `fatura kestik/düzenledik`, `hakediş çıkardık` =
  revenue accrual; `fatura geldi`, `malzeme aldık`, `harcadık` = expense; `Devrim'in hakedişinden 10 bin ödendi` = payment;
  `alacağını sildik` = receivable adjustment. Inferences are printed as notes in the draft.
* **Amounts** — written out (`yirmi beş bin`, `yarım milyon`, `bir buçuk milyon`), split (`10 bin 500`, `3 milyon 250 bin`), symbols (`$500`, `€750`, `₺10.000`).
  The original sentence (not the converted one) is what the audit trail stores.
* **Dates** — `geçen cuma`, `3 gün önce`, `1 hafta önce`, `salı günü`, `geçen hafta salı`, `ayın 5'inde`, `geçen ay 15'inde`, `12.10 tarihinde`, `dünkü`, `bu sabah`
  (past-oriented; an impossible day such as 31 in a 30-day month is reported, not guessed).
* **Account hints** — `kasadan/elden/nakit` → the cash account, `bankadan/havale/EFT` → the bank account, chosen only when exactly one fits
  (the hint is kept if the activity is answered later). Shown in the draft; never overrides an explicit account.
* **Names** — Turkish consonant softening (`Çakmak` → `Çakmağa`), a swapped letter (`Devirm` → suggests `Devrim`), `Holding adına …`.
  A fragment-with-amount sentence such as `Devrim 10 bin` or `Göktürk Okulu 10 bin` opens the wizard and asks the type.

### Never recorded (the sentence does not describe something that happened)
Negated (`ödeme yapmadık`, `yapılmadı`, `ödenmedi`, `ödeyemedik`, `ödemeyeceğiz`, `yapılmamış`), needed/wished (`yapmam lazım`, `gerekiyor`,
`yapabiliriz`, `ödeyelim`, `unuttum`, `istiyor`, `talep etti`, `bekliyor`, `planlandı`), conditional (`gerekirse`, `ödersek`) and future
(`ödeyeceğiz`, `yapılacak`) sentences never open a draft; they go to the existing advisor. A polite command (`… ekler misin?` with an amount) is
still a command, not a question.

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

**Everyday phrasing** (`tests/test_cfo_anlama.py`, `tests/test_cfo_sorgu.py`):
* *Question forms*: no `?` needed — `ne durumda`, `nasıl`, question particles (`var mı`, `ediyor muyuz`), a trailing `… ne`, `toplam …`,
  and amount-less plural nouns (`Devrim'in ödemeleri`, `tüm tahsilatlar`) are read requests (plural nouns also list the items).
* *Periods*: `bugün`, `dün`, `bu/geçen hafta`, `bu/geçen ay`, month names (also `Ekimde`, `Eylül'de`), `bu/geçen yıl`, `2025 yılında`,
  `son 30 günde`, `son 3 ayda`, `son bir yılda`, `ilk çeyrek`/`2. çeyrek`/`Q3`/`bu çeyrek`, `ilk 6 ay`, `yıl/ay başından beri`,
  ranges (`1 Ekim ile 10 Ekim arasında`, `1-10 Ekim`, `01.10.2026 - 10.10.2026`). The period is printed in the answer.
* *Metrics*: `gelir`/`ciro` = kazanılan gelir, `gelir gider farkı` = kâr/zarar, `harcadık` = ödeme (answer also shows gider).
* *Direction*: `İşveren bize ne kadar borçlu` = kalan alacak, `Devrim ne kadar alacaklı` = açık borç (we owe him),
  `İşveren ne kadar ödedi` = tahsilat (the customer's payment); `İşverene ne kadar ödedik` (to him) stays a payment.
* *Names*: a distinctive fragment of a project/activity (`Göktürk`) is accepted and the answer says so (`Not: …`). A **person/counterparty**
  fragment or typo is never silently chosen — the answer asks, with the single candidate named.

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
* Splitting one payment across several work items or counterparties in one sentence, or several records in one sentence
  (`Devrim'e 10 bin, Sebahattin'e 5 bin ödedik` is handed to the legacy path).
* Questions that need a new metric: rankings (`en çok kime ödeme yaptık`), account balances (`kasada ne var`), per-counterparty lists
  of open items. They fall through to the existing advisor.
* "Refund from a supplier" (`Devrim'den 2 bin TL geri aldık`) and other phrasings whose direction depends on context the sentence does not give.
* Natural-language document classification beyond the existing upload pipeline (unchanged).
* Metrics for Excel rows typed by hand appear only after `excel_operasyon.py defteri-aktar` (the Holding page warns).
* Currency defaults to TL when neither the sentence nor the chosen account says otherwise (always visible in the draft).

## PR-06 doğrulaması

`tests/test_final_product_acceptance.py`, talimatın deterministik taslağa,
parolalı onaya, Excel/SQLite defterine, rapora ve bağımsız recovery vault dry-run'a
ulaştığı yolu sentetik olarak doğrular. Aynı tutarlı farklı belgeler ayrı tutulur;
aynı belge tekrarında idempotency korunur. Test, gerçek LLM veya ağ çağrısı yapmaz.
