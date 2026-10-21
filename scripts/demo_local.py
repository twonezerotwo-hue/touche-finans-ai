"""İzole sentetik Touche Finans demosu.

Üretim yolunu ve OneDrive'ı reddeder; yalnızca yeni bir geçici kök altında
sentetik workbook/DB oluşturur. Bu dosya üretim sunucusunu başlatmak için
kullanılmamalıdır.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _safe_root(value: str | None) -> Path:
    root = Path(value) if value else Path("C:/Temp") / ("touche-finans-demo-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    resolved = root.resolve()
    blocked = (Path("C:/ai-excel").resolve(), Path.home() / "OneDrive")
    if any(resolved == x or x in resolved.parents for x in blocked):
        raise SystemExit(f"Demo kökü üretim/OneDrive yolu olamaz: {resolved}")
    if resolved.exists() and any(p.name not in {"stdout.log", "stderr.log"} for p in resolved.iterdir()):
        raise SystemExit(f"Demo kökü boş olmalı; mevcut klasör korunuyor: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def seed(root: Path) -> tuple[Path, Path]:
    (root / "veri").mkdir(exist_ok=True)
    for name in ("loglar", "temp", "gelen_evrak", "arsiv", "muhasebe_export"):
        (root / name).mkdir(exist_ok=True)
    tests = Path(__file__).resolve().parents[1] / "tests"
    sys.path.insert(0, str(tests))
    from yardimci_excel import tam_akilli_excel  # local synthetic fixture only

    main = root / "veri" / "Touche Finans Demo.xlsx"
    backup = root / "veri" / "Touche Finans Demo_backup.xlsx"
    tam_akilli_excel(main, faaliyet_adi="Gürsar İnşaat")
    shutil.copy2(main, backup)
    return main, backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic local demo; never production data")
    parser.add_argument("--root")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    root = _safe_root(args.root)
    main_file, backup_file = seed(root)
    os.environ.update({
        "TOUCHE_BASE_DIR": str(root),
        "TOUCHE_MAIN_WORKBOOK": str(main_file),
        "TOUCHE_BACKUP_WORKBOOK": str(backup_file),
        "FINANS_UI_PORT": str(args.port or 0),
        "TELEGRAM_BOT_TOKEN": "0:synthetic-demo-no-network",
        "TEXT_LLM_PROVIDER": "deterministic",
    })
    # calistir port=0 is not usable for its startup banner, so choose an OS
    # port before import and expose it in the environment.
    if not args.port:
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["FINANS_UI_PORT"] = str(sock.getsockname()[1])
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import finans_asistani_sunucu

    print(f"DEMO_ROOT={root}", flush=True)
    print(f"DEMO_MAIN={main_file}", flush=True)
    print(f"DEMO_BACKUP={backup_file}", flush=True)
    finans_asistani_sunucu.calistir(host="127.0.0.1", port=int(os.environ["FINANS_UI_PORT"]))


if __name__ == "__main__":
    main()
