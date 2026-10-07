# -*- coding: utf-8 -*-
"""SheetAgent yaklaşımıyla deterministik ve izinli aylık iş reçeteleri."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from akilli_excel import indeks_olustur
from config import FIRMALAR, LOGLAR, RECETE_DIR
from excel_kalite import excel_kalite_kontrol
from finans_cekirdegi import (
    muhasebe_csv_export, sistem_kontrolu, veritabani_yedekle, yonetim_ozeti,
)
from guvenlik import audit_yaz


RECIPE_DIR = Path(RECETE_DIR)
RUN_LOG = Path(LOGLAR) / "recete_runs.jsonl"
ALLOWED_STEPS = frozenset({
    "database_backup", "finance_check", "workbook_quality_all",
    "refresh_index_all", "accounting_export", "management_summary",
})


def _donem(value):
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", str(value or ""))
    if not match:
        raise ValueError("Dönem YYYY-AA biçiminde olmalı.")
    return int(match.group(1)), int(match.group(2))


def _recipe_path(name):
    if not re.fullmatch(r"[a-z0-9_]+", str(name or "")):
        raise ValueError("Geçersiz reçete adı.")
    path = RECIPE_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError("Reçete bulunamadı.")
    return path


def recete_yukle(name):
    data = json.loads(_recipe_path(name).read_text(encoding="utf-8"))
    if data.get("name") != name or not isinstance(data.get("steps"), list):
        raise ValueError("Reçete şeması geçersiz.")
    for step in data["steps"]:
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_STEPS:
            raise ValueError("Reçetede izin verilmeyen işlem var.")
        if any(x in step for x in ("code", "command", "script", "python", "m_expression")):
            raise ValueError("Reçeteler kod veya komut çalıştıramaz.")
    return data


def recete_listele():
    RECIPE_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(RECIPE_DIR.glob("*.json")):
        try:
            data = recete_yukle(path.stem)
            rows.append({"name": data["name"], "description": data.get("description", ""), "version": data.get("version", "")})
        except Exception:
            continue
    return rows


def _log(entry):
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def recete_calistir(name, parameters, actor="sistem"):
    recipe = recete_yukle(name)
    params = dict(parameters or {})
    missing = [x for x in recipe.get("parameters", []) if not params.get(x)]
    if missing:
        raise ValueError("Eksik reçete parametresi: " + ", ".join(missing))
    yil, ay = _donem(params.get("donem"))
    bas = date(yil, ay, 1)
    son = date(yil + (ay == 12), 1 if ay == 12 else ay + 1, 1) - timedelta(days=1)
    run = {"recipe": name, "version": recipe.get("version"), "actor": str(actor),
           "started_at": datetime.now().isoformat(timespec="seconds"), "parameters": params,
           "status": "RUNNING", "steps": [], "files": []}
    try:
        for step in recipe["steps"]:
            action = step["action"]
            if action == "database_backup":
                result = {"path": str(veritabani_yedekle())}
            elif action == "finance_check":
                result = sistem_kontrolu()
                if recipe.get("validation", {}).get("require_balanced_journals") and result["dengesiz_fis"]:
                    raise ValueError("Dengesiz muhasebe fişi bulundu.")
                if recipe.get("validation", {}).get("require_zero_sync_queue") and result["senkron_bekleyen"]:
                    raise ValueError("Merkezi deftere aktarılmayı bekleyen kayıt var.")
            elif action == "workbook_quality_all":
                result = {key: excel_kalite_kontrol(key, witan=False) for key in FIRMALAR}
                failures = [key for key, value in result.items() if value["yerel"]["durum"] == "FAIL"]
                if failures and recipe.get("validation", {}).get("stop_on_workbook_failure"):
                    raise ValueError("Excel kalite kontrolü başarısız: " + ", ".join(failures))
            elif action == "refresh_index_all":
                result = {key: {"chunks": indeks_olustur(key, zorla=True)["total_chunks"]} for key in FIRMALAR}
            elif action == "accounting_export":
                path = muhasebe_csv_export(bas, son)
                result = {"path": str(path)}
                run["files"].append(str(path))
            elif action == "management_summary":
                result = yonetim_ozeti(yil=yil, ay=ay)
            else:  # recete_yukle zaten engeller; savunma amaçlı.
                raise ValueError("İzin verilmeyen reçete işlemi.")
            run["steps"].append({"action": action, "status": "PASS", "result": result})
        run["status"] = "PASS"
    except Exception as exc:
        run["status"] = "FAIL"
        run["error"] = str(exc)
        run["steps"].append({"action": action if "action" in locals() else "load", "status": "FAIL", "error": str(exc)})
        raise
    finally:
        run["finished_at"] = datetime.now().isoformat(timespec="seconds")
        _log(run)
        audit_yaz("recete_calistirildi", recete=name, durum=run["status"], aktor=str(actor), donem=params.get("donem"))
    return run
