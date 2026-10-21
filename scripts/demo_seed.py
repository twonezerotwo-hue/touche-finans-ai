"""Sentetik Premium CFO demo verisi.

Bu modül yalnızca açıkça verilen geçici demo kökünde çalışır. Gerçek finans
dosyalarını keşfetmez; sentetik çalışma kitabı ve SQLite defteri üretir.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ACTIVITIES: dict[str, dict[str, Any]] = {
    "gursar_mimarlik": {"ad": "Gürsar Mimarlık", "faaliyet": "Gürsar Mimarlık", "holding_payi": 0.5},
    "touche_ticaret": {"ad": "Touche Ticaret", "faaliyet": "Touche Ticaret", "holding_payi": 1.0},
    "touche_hizmet": {"ad": "Touche Teknoloji", "faaliyet": "Touche Teknoloji", "holding_payi": 1.0},
}
PROJECTS = {
    "gursar_mimarlik": ["Göktürk Okulu", "Bahçeşehir Villa"],
    "touche_ticaret": ["Ankara Mağaza", "Bursa Depo"],
    "touche_hizmet": ["CFO Platformu", "Destek Sözleşmesi"],
}
COUNTERPARTIES = [
    ("Atlas Yapı", "MÜŞTERİ"), ("Kuzey Çelik", "TEDARİKÇİ"),
    ("Göktürk İşveren", "MÜŞTERİ"), ("Bursa Tedarik", "TEDARİKÇİ"),
    ("Anka Lojistik", "TEDARİKÇİ"), ("Mavi Market", "MÜŞTERİ"),
    ("Nova Yazılım", "TEDARİKÇİ"), ("Delta Bulut", "TEDARİKÇİ"),
    ("Ahmet Demir", "PERSONEL"), ("Mehmet Kaya", "TEDARİKÇİ"),
    ("Cemre Şensoy", "DİĞER"), ("Selin Finans", "FİNANS"),
    ("Ofis Market", "TEDARİKÇİ"), ("Vergi Dairesi", "KAMU"),
    ("Kredi Veren Banka", "BANKA"),
]


def _safe_root(root: Path) -> Path:
    resolved = Path(root).resolve()
    blocked = {Path(r"C:\ai-excel").resolve(), (Path.home() / "OneDrive").resolve()}
    if any(resolved == item or item in resolved.parents for item in blocked):
        raise ValueError(f"Demo kökü üretim/OneDrive olamaz: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _registry(root: Path) -> None:
    folder = root / "firmalar"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"surum": 1, "faaliyetler": {key: {**value, "excel_mode": "master", "pb": "TL"}
                                               for key, value in ACTIVITIES.items() if key != "gursar_mimarlik"}}
    (folder / "faaliyetler.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _workbook(path: Path) -> None:
    import sys
    tests = Path(__file__).resolve().parents[1] / "tests"
    sys.path.insert(0, str(tests))
    from openpyxl import load_workbook
    from yardimci_excel import tam_akilli_excel

    tam_akilli_excel(path, faaliyet_adi="Gürsar Mimarlık", ledger_formul_satiri=300)
    wb = load_workbook(path)
    ws = wb["99_Ayarlar"]
    for row, (key, data) in enumerate([("touche_holding", {"ad": "Touche Holding"}), *ACTIVITIES.items()], 13):
        ws[f"A{row}"] = data["ad"]
        ws[f"B{row}"] = key
        ws[f"C{row}"] = "Aktif"
        ws[f"D{row}"] = data.get("holding_payi", 1.0)
    for row, (activity, projects) in enumerate(PROJECTS.items(), 13):
        for project in projects:
            ws[f"H{row}"] = activity
            ws[f"I{row}"] = project
            row += 1
    accounts = [
        ("Gürsar TL Banka", "gursar_mimarlik", "TL"), ("Gürsar Kasa", "gursar_mimarlik", "TL"),
        ("Gürsar USD Banka", "gursar_mimarlik", "USD"), ("Touche Ticaret Banka", "touche_ticaret", "TL"),
        ("Ticaret Kasa", "touche_ticaret", "TL"), ("Teknoloji Banka", "touche_hizmet", "TL"),
        ("Teknoloji Kasa", "touche_hizmet", "TL"), ("Holding Ana Banka", "touche_holding", "TL"),
    ]
    for row, (name, activity, currency) in enumerate(accounts, 13):
        ws[f"L{row}"] = name; ws[f"M{row}"] = activity; ws[f"N{row}"] = currency
    for row, (name, kind) in enumerate(COUNTERPARTIES, 13):
        ws[f"AG{row}"] = name; ws[f"AH{row}"] = kind; ws[f"AI{row}"] = "DEMO"
    for row, (currency, rate) in enumerate([("TL", 1), ("USD", 40), ("EUR", 50), ("GBP", 60), ("USDT", 40)], 3):
        ws[f"AD{row}"] = currency; ws[f"AE{row}"] = rate
    kalem = wb["06_Proje İş Kalemleri"]
    items = [
        ("gursar_mimarlik", "Göktürk Okulu", "Taş işleri", 186240, 140000),
        ("gursar_mimarlik", "Göktürk Okulu", "Saha betonu", 140000, 100000),
        ("gursar_mimarlik", "Bahçeşehir Villa", "Çelik imalat", 210000, 275000),
        ("touche_ticaret", "Ankara Mağaza", "Stok alımı", 180000, 245000),
        ("touche_ticaret", "Ankara Mağaza", "Nakliye", 42000, 60000),
        ("touche_ticaret", "Bursa Depo", "Raf sistemi", 125000, 175000),
        ("touche_hizmet", "CFO Platformu", "Yazılım geliştirme", 260000, 420000),
        ("touche_hizmet", "CFO Platformu", "Bulut hizmeti", 80000, 120000),
        ("touche_hizmet", "Destek Sözleşmesi", "Danışmanlık", 90000, 180000),
        ("touche_hizmet", "Destek Sözleşmesi", "Personel", 95000, 140000),
        ("gursar_mimarlik", "Bahçeşehir Villa", "Elektrik", 90000, 130000),
        ("touche_ticaret", "Bursa Depo", "Sigorta", 70000, 95000),
    ]
    headers = {str(cell.value).strip(): cell.column for cell in kalem[5] if cell.value}
    for row, item in enumerate(items, 6):
        values = {"Faaliyet": item[0], "Proje": item[1], "İş Kalemi": item[2], "Bütçe": item[3], "Satış": item[4]}
        for key, value in values.items():
            if key in headers: kalem.cell(row, headers[key]).value = value
    ledger = wb["03_Kayıt Merkezi"]
    inbox = wb["05_AI Belge Inbox"]
    for row in range(6, 306):
        if ledger[f"S{row}"].value is None: ledger[f"S{row}"] = f'=IF(C{row}="","",C{row})'
        if inbox[f"Q{row}"].value is None: inbox[f"Q{row}"] = f'=IF(A{row}="","",A{row})'
    # Demo-only fixture writer; production workbook writes remain in excel_deposu.
    getattr(wb, "save")(path)


def _record(activity: str, project: str, counterparty: str, kind: str, amount: float,
            when: date, *, vade: date | None = None, pb: str = "TL", kur: float = 1,
            account: str = "Gürsar TL Banka", description: str = "") -> dict[str, Any]:
    types = {"revenue": ("Hakediş / Gelir Tahakkuku", "Müşteri"), "income": ("Tahsilat", "Müşteri"),
             "cost": ("Gider Tahakkuku", "Tedarikçi"), "payment": ("Usta/Tedarikçi Ödemesi", "Tedarikçi"),
             "loan": ("Kredi Kullanımı", "Banka"), "fee": ("Banka Masrafı", "Banka")}
    operation, ctype = types[kind]
    return {"Tarih": when.isoformat(), "Faaliyet": ACTIVITIES[activity]["ad"], "Proje": project,
            "Cari_Tipi": ctype, "Cari": counterparty, "Islem_Turu": operation, "Hesap_Kasa": account,
            "Tutar": round(amount, 2), "PB": pb, "Kur": kur, "Vade": vade.isoformat() if vade else "",
            "Durum": "Gerçekleşti" if kind in {"income", "payment", "loan", "fee"} else "Bekliyor",
            "Aciklama": description or f"Sentetik demo: {operation}", "_guven": 100, "AI_Guven": 100}


def _events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for month in range(6):
        first = date(2026, 4 + month, 1)
        for activity, projects in PROJECTS.items():
            for index, project in enumerate(projects):
                events.append(_record(activity, project, "Atlas Yapı" if index == 0 else "Mavi Market", "revenue", 145000 + month * 6000 + index * 12000, first))
                events.append(_record(activity, project, "Kuzey Çelik" if index == 0 else "Nova Yazılım", "cost", 76000 + month * 3500 + index * 5000, first + timedelta(days=3), vade=first + timedelta(days=30)))
                if month % 3 != 1:
                    events.append(_record(activity, project, "Atlas Yapı" if index == 0 else "Mavi Market", "income", 105000 + month * 4000 + index * 8000, first + timedelta(days=10), account="Holding Ana Banka" if activity == "touche_hizmet" else ("Gürsar TL Banka" if activity == "gursar_mimarlik" else "Touche Ticaret Banka")))
                events.append(_record(activity, project, "Kuzey Çelik" if index == 0 else "Nova Yazılım", "payment", 42000 + month * 2500 + index * 3500, first + timedelta(days=18), account="Gürsar TL Banka" if activity == "gursar_mimarlik" else ("Teknoloji Banka" if activity == "touche_hizmet" else "Touche Ticaret Banka")))
        events.append(_record("gursar_mimarlik", "Göktürk Okulu", "Kredi Veren Banka", "loan", 250000 + month * 10000, first + timedelta(days=5), account="Gürsar TL Banka", description="Sentetik işletme kredisi"))
        events.append(_record("touche_ticaret", "Ankara Mağaza", "Banka", "fee", 850 + month * 35, first + timedelta(days=20), account="Touche Ticaret Banka"))
    future_start = date(2026, 10, 26)
    for week in range(13):
        due = future_start + timedelta(days=week * 7)
        for activity, projects in PROJECTS.items():
            project = projects[week % 2]
            events.append(_record(activity, project, "Vergi Dairesi" if week % 2 else "Kuzey Çelik", "cost", 18000 + week * 750 + len(activity) * 100, due - timedelta(days=2), vade=due, account="Gürsar TL Banka" if activity == "gursar_mimarlik" else ("Teknoloji Banka" if activity == "touche_hizmet" else "Touche Ticaret Banka"), description="13 haftalık nakit planı"))
            events.append(_record(activity, project, "Atlas Yapı", "revenue", 32000 + week * 1100, due - timedelta(days=1), vade=due, description="Beklenen hakediş"))
    for i in range(3):
        events.append(_record("gursar_mimarlik", "Göktürk Okulu", "Banka", "cost", 90000 + i * 10000, date(2026, 6 + i, 22), vade=date(2026, 10 + i, 20), description="Bütçe üstü ek iş"))
    for i in range(4):
        events.append(_record("touche_hizmet", "CFO Platformu", "Delta Bulut", "payment", 12000 + i * 500, date(2026, 7, 8 + i), pb="USD", kur=40, account="Teknoloji Banka", description="USD bulut ödemesi"))
    return events


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()


def seed_financial_data(root: Path, main_file: Path, backup_file: Path, *, reuse: bool = False) -> dict[str, Any]:
    root = _safe_root(root)
    manifest_path = root / "demo_seed_manifest.json"
    if manifest_path.exists() and reuse:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    _registry(root)
    # Registry must be loaded before the synthetic fixture imports config.
    import config
    _workbook(main_file)
    backup_file.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(main_file, backup_file)
    from excel_yazici import yaz
    from finans_cekirdegi import acilis_bakiyesi_kaydet, finans_kaydi_yaz
    from veri_katmani import baglanti, veritabani_hazirla
    veritabani_hazirla()
    with baglanti() as con:
        now = date(2026, 4, 1).isoformat()
        for key, info in {"touche_holding": {"ad": "Touche Holding"}, **ACTIVITIES}.items():
            con.execute("INSERT OR IGNORE INTO faaliyet_alanlari(alan_id,ad,holding_key,guncelleme) VALUES(?,?,?,?)", (f"ALAN-{key}", info["ad"], "touche_holding", now))
            for name, kind in COUNTERPARTIES:
                con.execute("INSERT OR IGNORE INTO karsi_taraflar(taraf_id,ad,tip,faaliyet_key) VALUES(?,?,?,?)", (f"CP-{hashlib.sha1(name.encode()).hexdigest()[:10]}", name, kind, key))
        for key, projects in PROJECTS.items():
            for idx, project in enumerate(projects, 1):
                con.execute("INSERT OR IGNORE INTO sozlesmeler(sozlesme_id,faaliyet_key,alan_id,proje,musteri,tutar_kurus,para_birimi,durum,olusturan,olusturma) VALUES(?,?,?,?,?,?,?,?,?,?)", (f"SZ-{key}-{idx}", key, f"ALAN-{key}", project, "Atlas Yapı", 50000000, "TL", "AKTIF", "demo", now))
    for key, opening in [("touche_holding", 500000), ("gursar_mimarlik", 250000), ("touche_ticaret", 180000), ("touche_hizmet", 140000)]:
        try: acilis_bakiyesi_kaydet(key, "102", date(2026, 4, 1), opening, "demo-seed")
        except ValueError as exc:
            if "zaten" not in str(exc): raise
    events = _events()
    for offset in range(0, len(events), 20):
        batch = events[offset:offset + 20]
        by_activity: dict[str, list[dict[str, Any]]] = {}
        for item in batch:
            key = next(k for k, v in ACTIVITIES.items() if v["ad"] == item["Faaliyet"])
            by_activity.setdefault(key, []).append(item)
        for key, records in by_activity.items():
            doc = f"DEMO-{offset:04d}-{key}"
            prepared = {"firma_key": key, "belge_id": doc, "belge_hash": hashlib.sha256(doc.encode()).hexdigest(), "veri": {"kayitlar": records}, "dogrulama": {"durum": "PASS", "skor": 100}}
            result = yaz(key, {"_hedef": "ana_ledger", "kayitlar": records}, doc, aktor="demo-seed")
            if not result.get("basari"): raise RuntimeError(result.get("hata"))
            finans_kaydi_yaz(prepared, "demo-seed")
    config_path = root / "veri" / "demo_seed_manifest.json"
    from master_data import onbellegi_temizle
    onbellegi_temizle()
    with baglanti() as con:
        count = int(con.execute("SELECT COUNT(*) FROM fisler WHERE belge_id NOT LIKE 'OPEN-%'").fetchone()[0])
    # Demo başlangıcında MAIN ve BACKUP aynı sentetik checkpoint'i temsil eder.
    shutil.copy2(main_file, backup_file)
    manifest = {"seed_version": 1, "dataset_id": "TOUCHE-PREMIUM-DEMO-2026-10", "synthetic_only": True,
                "activities": 4, "projects": 6, "counterparties": len(COUNTERPARTIES), "ledger_events": len(events),
                "journal_entries": count, "main_workbook": str(main_file), "backup_workbook": str(backup_file),
                "database": str(config.FINANS_DB), "workbook_sha256": _sha(main_file), "backup_sha256": _sha(backup_file),
                "generated": "2026-10-21T00:00:00Z"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    config_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
