# -*- coding: utf-8 -*-
"""CFO testleri icin ortak izole ortam: test Smart Excel, gecici DB, audit, onay kuyrugu, LLM kapali."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import cfo_niyet
import cfo_sohbet
import cfo_varliklar
import config
import excel_deposu
import excel_yazici
import finans_cekirdegi as cekirdek
import guvenlik
import master_data
import ollama_client
import ui_belge_servisi as ui
import veri_katmani
from _ortam import AnaExcelYonlendirme
from yardimci_excel import ledger_satirlari, tam_akilli_excel

BUGUN = date(2026, 10, 12)
AKTOR = "local-admin"


def llm_cagrilamaz(*a, **k):
    raise AssertionError("Bu akışta LLM/ağ çağrısı yapılmamalıdır.")


class CfoTestTemeli(unittest.TestCase):
    faaliyet_adi = "Gürsar İnşaat"          # Excel 99_Ayarlar'daki yazim

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ana = tam_akilli_excel(self.root / "veri" / "ANA.xlsx", faaliyet_adi=self.faaliyet_adi)
        self.yedek = self.root / "yedek" / "ANA - YEDEK.xlsx"
        self.yonlendirme = AnaExcelYonlendirme(self.ana, self.yedek).baslat()
        self.audit = self.root / "audit.jsonl"
        self._yamalar = [
            patch.object(cekirdek, "DB_PATH", self.root / "finans.db"),
            patch.object(guvenlik, "AUDIT_LOG", self.audit),
            patch.object(guvenlik, "LOGLAR", self.root),
            patch.object(ui, "STATE_PATH", self.root / "ui_kuyruk.json"),
            patch.object(ui, "_onay_parolasini_dogrula"),
            patch.object(cfo_sohbet, "STATE_PATH", self.root / "cfo_taslaklari.json"),
            patch.object(ollama_client, "metne_sor", side_effect=llm_cagrilamaz),
            patch.object(ollama_client, "metne_sor_yedek", side_effect=llm_cagrilamaz),
            patch("requests.post", side_effect=llm_cagrilamaz),
        ]
        for y in self._yamalar:
            y.start()
        ui._BEKLEYEN = {}
        cfo_sohbet.sifirla(self.root / "cfo_taslaklari.json")
        master_data.onbellegi_temizle()
        veri_katmani._HAZIRLANAN.clear()
        veri_katmani.veritabani_hazirla()
        self._firmalar = {k: dict(v) for k, v in config.FIRMALAR.items()}

    def tearDown(self):
        for y in reversed(self._yamalar):
            y.stop()
        self.yonlendirme.bitir()
        master_data.onbellegi_temizle()
        self.temp.cleanup()

    # --- yardimcilar ---
    def katalog(self):
        return cfo_varliklar.katalog_yukle()

    def niyet(self, metin):
        return cfo_niyet.analiz_et(metin, self.katalog(), BUGUN)

    def baslat(self, metin, **kw):
        n = self.niyet(metin)
        return cfo_sohbet.baslat(n, AKTOR, self.katalog(), bugun=BUGUN, **kw)

    def sec(self, yanit, alan, deger):
        self.assertEqual(yanit["cfo"]["soru"]["alan"], alan, yanit["cevap"])
        return cfo_sohbet.cevapla(yanit["cfo"]["taslak_id"], alan, deger, AKTOR, "secim", self.katalog())

    def onayla(self, yanit):
        token = yanit["kayit_taslagi"]["token"]
        return ui.onayla(token, AKTOR, "test-parola")

    def gecmis_odeme_yaz(self, belge_id="GECMIS-1", cari="Devrim", hesap="Gürsar TL Banka", tutar=1000):
        """Cariye ait onceki kaydi ana Excel'e yazar (hesap gecmisinden cikarimi tohumlamak icin)."""
        kayit = {"Tarih": "2026-09-01", "Faaliyet": self.faaliyet_adi, "Proje": "Göktürk Okulu", "Cari_Tipi": "Usta",
                 "Cari": cari, "Islem_Turu": "Usta/Tedarikçi Ödemesi", "Hesap_Kasa": hesap, "Tutar": tutar,
                 "PB": "TL", "Kur": 1, "Aciklama": "geçmiş"}
        sonuc = excel_yazici.yaz("gursar_mimarlik", {"_hedef": "ana_ledger", "kayitlar": [kayit]}, belge_id)
        self.assertTrue(sonuc["basari"], sonuc)
        master_data.onbellegi_temizle()

    def ledger(self):
        return ledger_satirlari(self.ana)

    def audit_olaylari(self, olay=None):
        import json

        if not self.audit.exists():
            return []
        satirlar = [json.loads(x) for x in self.audit.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [x for x in satirlar if olay is None or x.get("olay") == olay]
