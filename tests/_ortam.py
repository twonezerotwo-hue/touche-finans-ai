# -*- coding: utf-8 -*-
"""Test yardimcilari: ana/yedek Excel yolunu merkezi config uzerinden yonlendirir.

Testler uretim dosyasina asla dokunmaz; her test kendi gecici klasorunde calisir.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import config  # noqa: E402


class AnaExcelYonlendirme:
    """``with`` veya setUp/tearDown ile ana + yedek Excel yolunu gecici dosyalara cevirir."""

    def __init__(self, ana, yedek=None):
        self.ana = Path(ana)
        self.yedek = Path(yedek) if yedek else self.ana.parent / f"{self.ana.stem} - YEDEK.xlsx"
        self._eski = None

    def baslat(self):
        self._eski = (config.MAIN_WORKBOOK_PATH, config.BACKUP_WORKBOOK_PATH,
                      {k: v.get("excel") for k, v in config.FIRMALAR.items()})
        config.ana_excel_ayarla(self.ana, self.yedek)
        return self

    def bitir(self):
        if self._eski is None:
            return
        ana, yedek, excel_alanlari = self._eski
        config.ana_excel_ayarla(ana, yedek)
        for key, deger in excel_alanlari.items():
            if key in config.FIRMALAR and deger is not None:
                config.FIRMALAR[key]["excel"] = deger
        self._eski = None

    __enter__ = baslat

    def __exit__(self, *a):
        self.bitir()
