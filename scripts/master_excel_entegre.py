# -*- coding: utf-8 -*-
"""Onaylanmış bir XLSX sürümünü tek master yoluna yedekli/atomik yerleştirir."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from config import ARSIV, MASTER_EXCEL


def _sha256(path: Path) -> str:
    ozet = hashlib.sha256()
    with path.open("rb") as dosya:
        for blok in iter(lambda: dosya.read(1024 * 1024), b""):
            ozet.update(blok)
    return ozet.hexdigest()


def entegre_et(kaynak: Path) -> tuple[Path, Path, str]:
    kaynak = kaynak.resolve(strict=True)
    hedef = MASTER_EXCEL.resolve(strict=True)
    if kaynak.suffix.lower() != ".xlsx" or hedef.suffix.lower() != ".xlsx":
        raise ValueError("Yalnız .xlsx dosyaları entegre edilebilir.")
    with zipfile.ZipFile(kaynak) as paket:
        if paket.testzip() is not None or "xl/workbook.xml" not in paket.namelist():
            raise ValueError("Kaynak geçerli bir XLSX paketi değil.")

    yedek_klasoru = (ARSIV / "excel_yedek").resolve()
    yedek_klasoru.mkdir(parents=True, exist_ok=True)
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    yedek = yedek_klasoru / f"Touche_Holding_master_once_{zaman}.xlsx"
    gecici = hedef.parent / f".{hedef.stem}.{uuid.uuid4().hex}.tmp.xlsx"
    shutil.copy2(hedef, yedek)
    try:
        shutil.copy2(kaynak, gecici)
        kaynak_ozeti = _sha256(kaynak)
        if _sha256(gecici) != kaynak_ozeti:
            raise RuntimeError("Geçici dosya SHA-256 doğrulaması başarısız.")
        with zipfile.ZipFile(gecici) as paket:
            if paket.testzip() is not None:
                raise RuntimeError("Geçici XLSX bütünlük doğrulaması başarısız.")
        os.replace(gecici, hedef)
        if _sha256(hedef) != kaynak_ozeti:
            shutil.copy2(yedek, hedef)
            raise RuntimeError("Değişim sonrası doğrulama başarısız; yedek geri yüklendi.")
        return hedef, yedek, kaynak_ozeti
    finally:
        gecici.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Kullanım: py master_excel_entegre.py <onaylanmis.xlsx>")
    master, yedek, ozet = entegre_et(Path(sys.argv[1]))
    print(f"MASTER={master}")
    print(f"BACKUP={yedek}")
    print(f"SHA256={ozet}")
