# -*- coding: utf-8 -*-
"""Tek ANA + tek YEDEK Excel deposu: guvenlik ve butunluk testleri.

Hicbir test uretim dosyasina dokunmaz; her test kendi gecici klasorunde calisir.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, Reference

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import config
import excel_deposu as depo
import excel_yazici
import guvenlik
from _ortam import AnaExcelYonlendirme
from yardimci_excel import ledger_satirlari, tam_akilli_excel


def kayit(**kw):
    taban = {"Tarih": "2026-10-12", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu",
             "Cari_Tipi": "Usta", "Cari": "Devrim", "Islem_Turu": "Usta/Tedarikçi Ödemesi",
             "Hesap_Kasa": "Gürsar TL Banka", "Tutar": 10000, "PB": "TL", "Kur": 1,
             "Aciklama": "Test ödemesi"}
    taban.update(kw)
    return taban


def veri(*kayitlar):
    return {"_hedef": "ana_ledger", "kayitlar": list(kayitlar) or [kayit()]}


class DepoTestTemeli(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ana = tam_akilli_excel(self.root / "veri" / "ANA.xlsx")
        self.yedek = self.root / "yedek" / "ANA - YEDEK.xlsx"
        self.yonlendirme = AnaExcelYonlendirme(self.ana, self.yedek).baslat()
        self.audit = self.root / "audit.jsonl"
        self._yamalar = [patch.object(guvenlik, "AUDIT_LOG", self.audit),
                         patch.object(guvenlik, "LOGLAR", self.root)]
        for y in self._yamalar:
            y.start()

    def tearDown(self):
        for y in reversed(self._yamalar):
            y.stop()
        self.yonlendirme.bitir()
        self.temp.cleanup()

    def xlsx_dosyalari(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.xlsx"))

    def audit_olaylari(self, olay=None):
        if not self.audit.exists():
            return []
        satirlar = [json.loads(x) for x in self.audit.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [x for x in satirlar if olay is None or x.get("olay") == olay]


class TekAnaTekYedekTestleri(DepoTestTemeli):
    def test_ilk_yazma_yedegi_olusturur_ve_tek_yedek_kalir(self):
        v0 = depo.sha256(self.ana)
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertTrue(sonuc["basari"], sonuc)
        self.assertNotEqual(depo.sha256(self.ana), v0)
        self.assertEqual(depo.sha256(self.yedek), v0)           # YEDEK = bir onceki surum
        self.assertEqual(self.xlsx_dosyalari(), ["veri/ANA.xlsx", "yedek/ANA - YEDEK.xlsx"])
        self.assertEqual(depo.gecici_dosyalar(), [])

    def test_her_yazma_yedegi_yeniler_yeni_kopya_uretmez(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        v1 = depo.sha256(self.ana)
        excel_yazici.yaz("gursar_mimarlik", veri(kayit(Tutar=2500)), "B-2")
        self.assertEqual(depo.sha256(self.yedek), v1)            # artik V1 yedek
        for i in range(3, 9):
            excel_yazici.yaz("gursar_mimarlik", veri(kayit(Tutar=100 * i)), f"B-{i}")
        self.assertEqual(self.xlsx_dosyalari(), ["veri/ANA.xlsx", "yedek/ANA - YEDEK.xlsx"])
        self.assertEqual(len(ledger_satirlari(self.ana)), 8)

    def test_reddedilen_islem_yedegi_ilerletmez(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        yedek_once, ana_once = depo.sha256(self.yedek), depo.sha256(self.ana)
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(kayit(Tarih=None)), "B-X")   # eksik alan
        self.assertFalse(sonuc["basari"])
        self.assertEqual(depo.sha256(self.yedek), yedek_once)
        self.assertEqual(depo.sha256(self.ana), ana_once)

    def test_ayni_belge_iki_kez_yazilmaz(self):
        self.assertTrue(excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")["basari"])
        ana_once = depo.sha256(self.ana)
        tekrar = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertFalse(tekrar["basari"])
        self.assertEqual(tekrar["hata_kodu"], "MUKERRER_BELGE")
        self.assertEqual(depo.sha256(self.ana), ana_once)
        self.assertEqual(len(ledger_satirlari(self.ana)), 1)

    def test_ayni_belge_ayni_kayit_kimligini_uretir(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-SABIT")
        ilk = ledger_satirlari(self.ana)[0]["Kayit_ID"]
        self.assertEqual(ilk, excel_yazici._kayit_id("AI-TH", "B-SABIT-001"))


class AtomiklikVeKorumaTestleri(DepoTestTemeli):
    def test_yerlestirme_hatasinda_ana_degismez_gecici_kalmaz(self):
        once = depo.sha256(self.ana)
        gercek = depo.os.replace

        def hata(kaynak, hedef):
            if Path(hedef) == self.ana:
                raise OSError("simule atomik replace hatasi")
            return gercek(kaynak, hedef)

        with patch.object(depo.os, "replace", side_effect=hata):
            sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertFalse(sonuc["basari"])
        self.assertEqual(depo.sha256(self.ana), once)
        self.assertEqual(depo.gecici_dosyalar(), [])

    def test_excel_acikken_anlasilir_mesaj_verir(self):
        gercek = depo.os.replace

        def kilitli(kaynak, hedef):
            if Path(hedef) == self.ana:
                raise PermissionError(13, "Permission denied")
            return gercek(kaynak, hedef)

        with patch.object(depo.os, "replace", side_effect=kilitli):
            sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertFalse(sonuc["basari"])
        self.assertIn("tüm Excel pencerelerini kapatın", sonuc["hata"])
        self.assertEqual(sonuc["hata_kodu"], "ANA_KILITLI")

    def test_formul_hucresi_ezilemez(self):
        once = depo.sha256(self.ana)

        def kotu(wb):
            wb["03_Kayıt Merkezi"]["S6"] = 1            # formul yerine sabit deger
            return {"basari": True}

        with self.assertRaises(depo.DepoHatasi) as cm:
            depo.guvenli_degistir(kotu, kaynak="test")
        self.assertEqual(cm.exception.kod, "VERI_KORUMA")
        self.assertIn("Formül bozuldu", cm.exception.mesaj)
        self.assertEqual(depo.sha256(self.ana), once)
        self.assertFalse(self.yedek.exists())            # basarisiz islem yedegi de olusturmaz

    def test_mevcut_veri_satiri_ezilemez(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        once, yedek_once = depo.sha256(self.ana), depo.sha256(self.yedek)

        def kotu(wb):
            wb["03_Kayıt Merkezi"]["I6"] = 1             # mevcut tutari ez
            return {"basari": True}

        with self.assertRaises(depo.DepoHatasi) as cm:
            depo.guvenli_degistir(kotu, kaynak="test")
        self.assertEqual(cm.exception.kod, "VERI_KORUMA")
        self.assertEqual(depo.sha256(self.ana), once)
        self.assertEqual(depo.sha256(self.yedek), yedek_once)

    def test_izinli_sutun_degisebilir_baska_sutun_degisemez(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")

        def iptal(wb):
            wb["03_Kayıt Merkezi"]["V6"] = "İptal"
            return {"basari": True}

        sonuc = depo.guvenli_degistir(iptal, kaynak="test",
                                      degisim_izni={"03_Kayıt Merkezi": {"V"}})
        self.assertTrue(sonuc["basari"])

        def fazla(wb):
            wb["03_Kayıt Merkezi"]["V6"] = "Aktif"
            wb["03_Kayıt Merkezi"]["I6"] = 5
            return {"basari": True}

        with self.assertRaises(depo.DepoHatasi):
            depo.guvenli_degistir(fazla, kaynak="test", degisim_izni={"03_Kayıt Merkezi": {"V"}})

    def test_kismen_dolu_satirin_uzerine_yazilmaz(self):
        wb = load_workbook(self.ana)
        ws = wb["03_Kayıt Merkezi"]
        ws["F6"] = "Elle girilmiş not"            # A bos, ama baska sutun dolu -> kismen dolu
        wb.save(self.ana)
        wb.close()
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertTrue(sonuc["basari"], sonuc)
        wb = load_workbook(self.ana)
        ws = wb["03_Kayıt Merkezi"]
        self.assertEqual(ws["F6"].value, "Elle girilmiş not")
        self.assertIsNone(ws["A6"].value)
        self.assertEqual(ws["F7"].value, "Devrim")           # bir sonraki bos satira yazildi
        self.assertTrue(str(ws["S7"].value).startswith("=IF"))
        wb.close()

    def test_kapasite_dolunca_reddeder_ve_hicbir_sey_degismez(self):
        with patch.object(config, "LEDGER_SON_SATIR", 7):
            self.assertTrue(excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")["basari"])
            self.assertTrue(excel_yazici.yaz("gursar_mimarlik", veri(), "B-2")["basari"])
            once = depo.sha256(self.ana)
            sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-3")
        self.assertFalse(sonuc["basari"])
        self.assertEqual(sonuc["hata_kodu"], "KAPASITE_DOLU")
        self.assertEqual(depo.sha256(self.ana), once)

    def test_islem_sirasinda_disaridan_degisen_ana_ezilmez(self):
        once = depo.sha256(self.ana)

        def kullanici_eszamanli_kaydetti(wb):
            # Kullanici ayni anda Excel'de dosyayi degistirip kaydetti.
            baska = load_workbook(self.ana)
            baska["99_Ayarlar"]["A30"] = "Kullanıcının eşzamanlı değişikliği"
            baska.save(self.ana)
            baska.close()
            wb["99_Ayarlar"]["A31"] = "Bizim değişikliğimiz"
            return {"basari": True}

        with self.assertRaises(depo.DepoHatasi) as cm:
            depo.guvenli_degistir(kullanici_eszamanli_kaydetti, kaynak="test")
        self.assertEqual(cm.exception.kod, "ANA_DEGISTI")
        wb = load_workbook(self.ana)
        self.assertEqual(wb["99_Ayarlar"]["A30"].value, "Kullanıcının eşzamanlı değişikliği")
        self.assertIsNone(wb["99_Ayarlar"]["A31"].value)
        wb.close()
        self.assertNotEqual(depo.sha256(self.ana), once)
        self.assertEqual(depo.gecici_dosyalar(), [])

    def test_bozuk_ana_yedegi_ezmez(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        yedek_once = depo.sha256(self.yedek)
        self.ana.write_bytes(b"bozuk dosya")
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-2")
        self.assertFalse(sonuc["basari"])
        self.assertEqual(sonuc["hata_kodu"], "DOSYA_BOZUK")
        self.assertEqual(depo.sha256(self.yedek), yedek_once)

    def test_openpyxl_in_koruyamadigi_parca_varsa_yazma_reddedilir(self):
        """Gomulu nesne/dilimleyici/VBA gibi parcalar openpyxl'de kaybolur; sessiz kayip yok."""
        import shutil
        import zipfile

        gecici = self.ana.with_name("enjekte.xlsx")
        with zipfile.ZipFile(self.ana) as kaynak, zipfile.ZipFile(gecici, "w", zipfile.ZIP_DEFLATED) as hedef:
            for oge in kaynak.infolist():
                hedef.writestr(oge, kaynak.read(oge.filename))
            hedef.writestr("xl/embeddings/oleObject1.bin", b"gomulu nesne")
        shutil.move(gecici, self.ana)
        once = depo.sha256(self.ana)
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertFalse(sonuc["basari"])
        self.assertEqual(sonuc["hata_kodu"], "PAKET_KAYBI")
        self.assertIn("xl/embeddings", sonuc["hata"])
        self.assertEqual(depo.sha256(self.ana), once)
        self.assertFalse(self.yedek.exists())

    def test_grafik_openpyxl_ile_korunur_ve_yazma_calisir(self):
        wb = load_workbook(self.ana)
        ws = wb["99_Ayarlar"]
        for i in range(1, 5):
            ws[f"AK{i}"] = i
        grafik = BarChart()
        grafik.add_data(Reference(ws, min_col=37, min_row=1, max_row=4))
        ws.add_chart(grafik, "AM2")
        wb.save(self.ana)
        wb.close()
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertTrue(sonuc["basari"], sonuc)
        import zipfile
        with zipfile.ZipFile(self.ana) as z:
            self.assertTrue(any(n.startswith("xl/charts/") for n in z.namelist()))

    def test_kuru_calistirma_hicbir_sey_degistirmez(self):
        once = depo.sha256(self.ana)
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1", kuru_calistir=True)
        self.assertTrue(sonuc["basari"])
        self.assertFalse(sonuc["degisiklik_yapildi"])
        self.assertEqual(depo.sha256(self.ana), once)
        self.assertFalse(self.yedek.exists())

    def test_artik_gecici_dosya_sonraki_islemde_silinir_baska_dosyaya_dokunulmaz(self):
        artik = self.ana.parent / f".{self.ana.stem}.{'a' * 32}.tmp.xlsx"
        artik.write_bytes(b"x")
        baska = self.ana.parent / ".baska-dosya.tmp.xlsx"
        baska.write_bytes(b"y")
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        self.assertFalse(artik.exists())
        self.assertTrue(baska.exists())

    def test_formul_enjeksiyonu_metin_olarak_yazilir(self):
        excel_yazici.yaz("gursar_mimarlik", veri(kayit(Aciklama="=HYPERLINK(\"http://x\")")), "B-1")
        wb = load_workbook(self.ana)
        h = wb["03_Kayıt Merkezi"]["X6"]
        self.assertEqual(h.data_type, "s")
        wb.close()

    def test_kilit_ic_ice_cagrida_kilitlenmez(self):
        from kayit_kilidi import kilitli

        @kilitli
        def dis():
            return excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")

        self.assertTrue(dis()["basari"])


class GeriDonusTestleri(DepoTestTemeli):
    def _iki_surum(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        excel_yazici.yaz("gursar_mimarlik", veri(kayit(Tutar=777)), "B-2")
        return depo.sha256(self.ana), depo.sha256(self.yedek)

    def test_geri_donus_ana_ile_yedegi_takas_eder(self):
        v2, v1 = self._iki_surum()
        sonuc = depo.yedekten_geri_don(aktor="ops", neden="test", onay="GERI-DON")
        self.assertTrue(sonuc["basari"])
        self.assertEqual(depo.sha256(self.ana), v1)          # ANA = V1
        self.assertEqual(depo.sha256(self.yedek), v2)        # YEDEK = V2 (uclu dosya yok)
        self.assertEqual(self.xlsx_dosyalari(), ["veri/ANA.xlsx", "yedek/ANA - YEDEK.xlsx"])
        self.assertEqual(len(ledger_satirlari(self.ana)), 1)
        olay = self.audit_olaylari("excel_geri_donus")[-1]
        self.assertEqual(olay["durum"], "BASARILI")
        self.assertEqual(olay["ana_hash_once"], v2)
        self.assertEqual(olay["ana_hash_sonra"], v1)

    def test_iki_kez_geri_donus_ilk_surume_doner(self):
        v2, v1 = self._iki_surum()
        depo.yedekten_geri_don(aktor="ops", neden="a", onay="GERI-DON")
        depo.yedekten_geri_don(aktor="ops", neden="b", onay="GERI-DON")
        self.assertEqual(depo.sha256(self.ana), v2)

    def test_onay_olmadan_geri_donulmez(self):
        v2, v1 = self._iki_surum()
        for onay in ("", "evet", "geri-don", None):
            with self.assertRaises(depo.DepoHatasi):
                depo.yedekten_geri_don(aktor="ops", neden="x", onay=onay)
        with self.assertRaises(depo.DepoHatasi):
            depo.yedekten_geri_don(aktor="", neden="x", onay="GERI-DON")
        self.assertEqual(depo.sha256(self.ana), v2)

    def test_ayni_surumde_geri_donus_reddedilir(self):
        excel_yazici.yaz("gursar_mimarlik", veri(), "B-1")
        depo.yedegi_yenile()
        with self.assertRaises(depo.DepoHatasi) as cm:
            depo.yedekten_geri_don(aktor="ops", neden="x", onay="GERI-DON")
        self.assertEqual(cm.exception.kod, "AYNI_SURUM")

    def test_yapay_zeka_http_ve_telegram_katmani_geri_donusu_cagiramaz(self):
        """Geri donus yalniz operasyon aracindadir; hicbir otonom katman onu icermez."""
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        izinli = {"excel_deposu.py", "excel_operasyon.py"}
        for dosya in scripts.glob("*.py"):
            if dosya.name in izinli:
                continue
            self.assertNotIn("yedekten_geri_don", dosya.read_text(encoding="utf-8"), dosya.name)
        ui = Path(__file__).resolve().parents[1] / "ui"
        for dosya in ui.glob("*.js"):
            self.assertNotIn("geri-don", dosya.read_text(encoding="utf-8").lower(), dosya.name)


class HariciDosyaVeAuditTestleri(DepoTestTemeli):
    def test_harici_dosya_ana_olur_tek_yedek_korunur(self):
        yeni = tam_akilli_excel(self.root / "disarida" / "onayli.xlsx")
        wb = load_workbook(yeni)
        wb["99_Ayarlar"]["A40"] = "Harici düzenleme"
        wb.save(yeni)
        wb.close()
        once = depo.sha256(self.ana)
        sonuc = depo.harici_dosyayi_ana_yap(yeni, aktor="ops", neden="test")
        self.assertTrue(sonuc["basari"])
        self.assertEqual(depo.sha256(self.ana), depo.sha256(yeni))
        self.assertEqual(depo.sha256(self.yedek), once)
        self.assertEqual(sorted(p.name for p in (self.root / "veri").glob("*")), ["ANA.xlsx"])

    def test_audit_kaydi_islem_izini_icerir(self):
        baglam = {"orijinal_talimat": "Göktürk okuluna Devrim'e 10 bin TL ödeme yapıldı.",
                  "normalize_komut": "ODEME|gursar|goktur|devrim|10000", "onay_kaynagi": "ui:test",
                  "taslak_id": "TSL-1"}
        sonuc = excel_yazici.yaz("gursar_mimarlik", veri(), "B-1", aktor="owner", baglam=baglam)
        self.assertTrue(sonuc["basari"])
        olay = self.audit_olaylari("excel_islem")[-1]
        self.assertEqual(olay["durum"], "BASARILI")
        self.assertEqual(olay["aktor"], "owner")
        self.assertRegex(olay["islem_id"], r"^OP-")
        self.assertEqual(olay["ana_hash_sonra"], depo.sha256(self.ana))
        self.assertEqual(olay["yedek_hash"], olay["ana_hash_once"])
        self.assertEqual(olay["orijinal_talimat"], baglam["orijinal_talimat"])
        self.assertEqual(olay["normalize_komut"], baglam["normalize_komut"])
        kayit0 = olay["kayitlar"][0]
        self.assertEqual(kayit0["transaction_id"], ledger_satirlari(self.ana)[0]["Kayit_ID"])
        self.assertEqual(kayit0["cari"], "Devrim")
        self.assertEqual(kayit0["proje"], "Göktürk Okulu")
        self.assertEqual(kayit0["tutar"], 10000)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", olay["kayit_hash"]))
        self.assertTrue(guvenlik.audit_zincirini_dogrula(self.audit)["gecerli"])

    def test_reddedilen_islem_de_denetlenir(self):
        excel_yazici.yaz("gursar_mimarlik", veri(kayit(Tarih=None)), "B-RED")
        olay = self.audit_olaylari("excel_islem")[-1]
        self.assertEqual(olay["durum"], "REDDEDILDI")
        self.assertIn("eksik", olay["hata"])


if __name__ == "__main__":
    unittest.main()
