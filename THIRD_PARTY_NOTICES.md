# Third-party components

This project uses or adapts ideas from the following open-source projects.

- excel-parser, commit `e513763cc359d91c7ad511ca2659acbb60593d10`, MIT License: https://github.com/knowledgestack/excel-parser
- Microsoft SheetBrain, commit `68fdda3c52603ce4f67b22f32c787c5cdcc1c59d`, MIT License: https://github.com/microsoft/SheetBrain
- SheetAgent, commit `0885891da8fa83cf5263778c4d3fa5c733e7cf32`, MIT License: https://github.com/hvithalani/sheetagent
- witan-cli / witan Python package 0.14.0, Apache-2.0 License: https://github.com/witanlabs/witan-cli

SheetBrain and SheetAgent are not bundled wholesale. The local implementation uses their documented architectural patterns: deny-by-default actions, understand-execute-validate flow, deterministic recipes, and post-run validation.

## CFO Operating System katmanı

CFO Operating System katmanı (finans motoru, senaryo motoru, belge hattı, AR/AP,
13 haftalık nakit, FP&A, AI CFO) bu depoda **konsept/mimari/UX referansı** olarak
incelenen açık kaynak projelerden esinlenmiştir; **kod kopyalanmamıştır**. Kavramlar
mevcut Python + SQLite + Vanilla JS stack'i içinde yeniden uygulanmıştır.

Referans alınan projeler (yalnız benchmark; kod alınmadı):

- Actual Budget (MIT) — bütçe/envelope modeli
- Firefly III (AGPL-3.0) — kişisel/kurumsal finans kavramları (AGPL olduğu için kod temiz-oda ile üretilmiştir, kopyalanmamıştır)
- ERPNext / Frappe Books (GPL) — hiyerarşi ve muhasebe kavramları (kod alınmadı)
- 13-week cash flow forecasting yaklaşımları (kamuya açık desenler)

Not: AGPL/GPL lisanslı projelerden kod kopyalanmamıştır; bu depo proprietary
olabileceği için bu kısıta uyulmuştur.

