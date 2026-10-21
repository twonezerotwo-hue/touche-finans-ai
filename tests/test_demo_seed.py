from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_demo_seed_creates_rich_synthetic_checkpoint(tmp_path):
    root = tmp_path / "demo"
    script = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); "
        "from demo_seed import seed_financial_data; r=Path(sys.argv[1]); "
        "print(seed_financial_data(r, r/'veri'/'main.xlsx', r/'veri'/'backup.xlsx'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(Path(__file__).parents[1] / "scripts")],
        check=True, capture_output=True, text=True,
        env={**__import__("os").environ, "TOUCHE_BASE_DIR": str(root),
             "TOUCHE_MAIN_WORKBOOK": str(root / "veri" / "main.xlsx"),
             "TOUCHE_BACKUP_WORKBOOK": str(root / "veri" / "backup.xlsx"),
             "TOUCHE_LEDGER_SON_SATIR": "300", "WITAN_REMOTE_ENABLED": "0"},
    )
    manifest = json.loads((root / "demo_seed_manifest.json").read_text(encoding="utf-8"))
    assert manifest["synthetic_only"] is True
    assert manifest["activities"] == 4
    assert manifest["projects"] == 6
    assert manifest["counterparties"] == 15
    assert manifest["ledger_events"] >= 150
    with sqlite3.connect(root / "veri" / "finans.db") as con:
        assert con.execute("select count(*) from fisler where belge_id not like 'OPEN-%'").fetchone()[0] >= 150
        assert con.execute("select count(*) from faaliyetler").fetchone()[0] == 4
    assert hashlib.sha256((root / "veri" / "main.xlsx").read_bytes()).hexdigest() == manifest["workbook_sha256"]
    assert (root / "veri" / "main.xlsx").read_bytes() == (root / "veri" / "backup.xlsx").read_bytes()
    assert "C:\\\\ai-excel" not in result.stdout


def test_demo_seed_reuse_is_idempotent(tmp_path):
    root = tmp_path / "demo"
    script = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); "
        "from demo_seed import seed_financial_data; r=Path(sys.argv[1]); "
        "a=seed_financial_data(r,r/'veri'/'main.xlsx',r/'veri'/'backup.xlsx'); "
        "b=seed_financial_data(r,r/'veri'/'main.xlsx',r/'veri'/'backup.xlsx',reuse=True); "
        "print(a['workbook_sha256']==b['workbook_sha256'])"
    )
    env = {**__import__("os").environ, "TOUCHE_BASE_DIR": str(root),
           "TOUCHE_MAIN_WORKBOOK": str(root / "veri" / "main.xlsx"),
           "TOUCHE_BACKUP_WORKBOOK": str(root / "veri" / "backup.xlsx"),
           "TOUCHE_LEDGER_SON_SATIR": "300", "WITAN_REMOTE_ENABLED": "0"}
    result = subprocess.run([sys.executable, "-c", script, str(root), str(Path(__file__).parents[1] / "scripts")],
                            check=True, capture_output=True, text=True, env=env)
    assert result.stdout.strip().endswith("True")
