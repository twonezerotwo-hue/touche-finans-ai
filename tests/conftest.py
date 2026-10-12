# -*- coding: utf-8 -*-
"""Test izolasyonu: testler hicbir zaman uretim Excel'ine veya verisine dokunmaz.

Bu dosya, ``config`` ilk kez ice aktarilmadan ONCE ortam degiskenlerini gecici bir
klasore yonlendirir. Ayrica uretim yolunun kullanilma ihtimaline karsi oturum
basinda sert bir kontrol yapar.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_KOK = Path(tempfile.mkdtemp(prefix="touche_test_"))
os.environ["TOUCHE_BASE_DIR"] = str(_KOK)
os.environ["TOUCHE_MAIN_WORKBOOK"] = str(_KOK / "veri" / "TEST Touche Holding.xlsx")
os.environ["TOUCHE_BACKUP_WORKBOOK"] = str(_KOK / "yedek" / "TEST Touche Holding - YEDEK.xlsx")
# Telegram modulu ice aktarilirken token ister; testte sahte (ag erisimi olmayan) deger.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test-token-no-network")
for _alt in ("loglar", "veri", "temp", "yedek"):
    (_KOK / _alt).mkdir(parents=True, exist_ok=True)
atexit.register(lambda: shutil.rmtree(_KOK, ignore_errors=True))

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def pytest_sessionstart(session):  # noqa: D103
    import config

    ana = str(config.ana_excel_yolu()).replace("\\", "/")
    if not ana.startswith(str(_KOK).replace("\\", "/")):
        raise RuntimeError(f"Test oturumu uretim Excel yoluna isaret ediyor: {ana}")
    if "OneDrive" in ana or "twone" in ana:
        raise RuntimeError(f"Test oturumu kullanici klasorune isaret ediyor: {ana}")
