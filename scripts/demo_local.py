"""İzole sentetik Touche Finans demosu.

Üretim yolunu ve OneDrive'ı reddeder; yalnızca yeni bir geçici kök altında
sentetik workbook/DB oluşturur. Bu dosya üretim sunucusunu başlatmak için
kullanılmamalıdır.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _safe_root(value: str | None, *, reuse: bool = False) -> Path:
    root = Path(value) if value else Path("C:/Temp") / ("touche-finans-demo-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    resolved = root.resolve()
    blocked = (Path("C:/ai-excel").resolve(), Path.home() / "OneDrive")
    if any(resolved == x or x in resolved.parents for x in blocked):
        raise SystemExit(f"Demo kökü üretim/OneDrive yolu olamaz: {resolved}")
    if resolved.exists() and reuse and (resolved / "demo_seed_manifest.json").exists():
        return resolved
    if resolved.exists() and any(p.name not in {"stdout.log", "stderr.log", "demo.pid"} for p in resolved.iterdir()):
        raise SystemExit(f"Demo kökü boş olmalı; mevcut klasör korunuyor: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def seed(root: Path, *, reuse: bool = False) -> tuple[Path, Path, dict]:
    (root / "veri").mkdir(exist_ok=True)
    for name in ("loglar", "temp", "gelen_evrak", "arsiv", "muhasebe_export"):
        (root / name).mkdir(exist_ok=True)
    main = root / "veri" / "Touche Finans Demo.xlsx"
    backup = root / "veri" / "Touche Finans Demo_backup.xlsx"
    from demo_seed import seed_financial_data
    manifest = seed_financial_data(root, main, backup, reuse=reuse)
    return main, backup, manifest


def _ollama_probe() -> tuple[str, str]:
    """Yerel Ollama'yı gerçek bir JSON yanıtıyla doğrular; ağ dışı sağlayıcı yoktur."""
    base = "http://127.0.0.1:11434"
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=3) as response:
            tags = json.loads(response.read().decode("utf-8"))
        names = [str(x.get("name") or "") for x in tags.get("models", [])]
        preferred = next((x for x in ("qwen3:8b", "qwen2.5vl:7b", "qwen3:4b") if x in names), next((x for x in names if "qwen" in x.lower()), ""))
        if not preferred:
            return "deterministic", "Ollama'da Qwen modeli bulunamadı"
        payload = json.dumps({"model": preferred, "prompt": 'Return only JSON: {"ok":true}', "format": "json", "stream": False, "think": False, "options": {"temperature": 0, "num_predict": 30}}).encode("utf-8")
        request = urllib.request.Request(base + "/api/generate", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.loads(response.read().decode("utf-8"))
        json.loads(str(result.get("response") or "{}"))
        return "ollama", preferred
    except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return "deterministic", f"Ollama kullanılamıyor: {type(exc).__name__}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic local demo; never production data")
    parser.add_argument("--root")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--reuse", action="store_true", help="Mevcut sentetik demo manifestini yeniden kullan")
    args = parser.parse_args()
    root = _safe_root(args.root, reuse=args.reuse)
    # Demo kökü ve registry, config import edilmeden önce hazırlanır.
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    os.environ.update({
        "TOUCHE_BASE_DIR": str(root),
        "TOUCHE_MAIN_WORKBOOK": str(root / "veri" / "Touche Finans Demo.xlsx"),
        "TOUCHE_BACKUP_WORKBOOK": str(root / "veri" / "Touche Finans Demo_backup.xlsx"),
        "TOUCHE_LEDGER_SON_SATIR": "300",
        "WITAN_REMOTE_ENABLED": "0",
    })
    main_file, backup_file, manifest = seed(root, reuse=args.reuse)
    provider, model = _ollama_probe()
    os.environ.update({
        "TOUCHE_BASE_DIR": str(root),
        "TOUCHE_MAIN_WORKBOOK": str(main_file),
        "TOUCHE_BACKUP_WORKBOOK": str(backup_file),
        "FINANS_UI_PORT": str(args.port or 0),
        "TELEGRAM_BOT_TOKEN": "0:synthetic-demo-no-network",
        "TEXT_LLM_PROVIDER": provider,
        "OLLAMA_TEXT_MODEL": model if provider == "ollama" else "qwen3:8b",
        "DEMO_MODE": "1",
        "DEMO_QWEN_STATUS": model,
        "TOUCHE_LEDGER_SON_SATIR": "300",
        "WITAN_REMOTE_ENABLED": "0",
    })
    os.environ.pop("EVREN_API_KEY", None)
    # calistir port=0 is not usable for its startup banner, so choose an OS
    # port before import and expose it in the environment.
    if not args.port:
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["FINANS_UI_PORT"] = str(sock.getsockname()[1])
    import finans_asistani_sunucu

    print(f"DEMO_ROOT={root}", flush=True)
    print(f"DEMO_MAIN={main_file}", flush=True)
    print(f"DEMO_BACKUP={backup_file}", flush=True)
    print(f"DEMO_DATASET={manifest.get('dataset_id')}", flush=True)
    print(f"DEMO_QWEN={provider}:{model}", flush=True)
    print(f"DEMO_URL=http://127.0.0.1:{os.environ['FINANS_UI_PORT']}", flush=True)
    finans_asistani_sunucu.calistir(host="127.0.0.1", port=int(os.environ["FINANS_UI_PORT"]))


if __name__ == "__main__":
    main()
