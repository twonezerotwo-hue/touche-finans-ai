# -*- coding: utf-8 -*-
"""Gercek tarayici (Playwright/Chromium) E2E: Holding -> Faaliyet -> Proje gezinmesi ve CFO sihirbazi.

Yalniz IZOLE test Smart Excel'i + gecici DB kullanir (CfoTestTemeli); uretim dosyalarina dokunmaz.
Playwright ya da Chromium yoksa testler atlanir (CI'da zorunlu degildir):
    pip install playwright   # tarayici ikilisi: PLAYWRIGHT_BROWSERS_PATH veya TOUCHE_TEST_CHROMIUM
"""
import glob
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import finans_cekirdegi as cekirdek
import ui_yetki
from cfo_test_temeli import CfoTestTemeli

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                            # pragma: no cover
    sync_playwright = None


def _tarayici_yolu():
    """Acik verilen ikili (TOUCHE_TEST_CHROMIUM) > hazir headless_shell (Linux/CI) > None (Playwright'in kendi
    kurulumu: Windows'ta ``py -3.12 -m playwright install chromium``)."""
    ozel = os.environ.get("TOUCHE_TEST_CHROMIUM")
    if ozel and os.path.exists(ozel):
        return ozel
    kok = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    adaylar = sorted(glob.glob(os.path.join(kok, "chromium_headless_shell-*", "chrome-linux", "headless_shell")))
    return adaylar[-1] if adaylar else None


YOL = _tarayici_yolu()
KOMUT = "Göktürk okuluna Gürsar Mimarlık adına Devrim'e 10 bin TL ödeme yapıldı."


@unittest.skipIf(sync_playwright is None, "Playwright kurulu değil")
class TarayiciTestleri(CfoTestTemeli):
    @classmethod
    def setUpClass(cls):
        import finans_asistani_sunucu as srv

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        try:
            secenekler = {"executable_path": YOL} if YOL else {}
            cls.tarayici = cls.pw.chromium.launch(args=["--no-sandbox"], **secenekler)
        except Exception as exc:                       # tarayici ikilisi yok: testler atlanir, sunucu kapatilir
            cls.pw.stop()
            cls.server.shutdown()
            cls.server.server_close()
            raise unittest.SkipTest(f"Chromium acilamadi ({type(exc).__name__}); Windows'ta: py -3.12 -m playwright install chromium")

    @classmethod
    def tearDownClass(cls):
        cls.tarayici.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        self._y2 = [patch.object(ui_yetki, "YETKI_DOSYASI", self.root / "ui_yetkililer.json"),
                    patch.object(ui_yetki, "ROLLER_DOSYASI", self.root / "ui_roller.json")]
        for y in self._y2:
            y.start()
        cekirdek.finans_kaydi_yaz({"belge_id": "H1", "firma_key": "gursar_mimarlik", "tur": "finans_belgesi",
                                   "veri": {"kayitlar": [{"Tarih": "2026-09-01", "Islem_Turu": "Hakediş / Gelir Tahakkuku",
                                                          "Tutar": 500_000, "PB": "TL", "Kur": 1, "Cari": "İşveren",
                                                          "Proje": "Göktürk Okulu"}]}}, "t")
        self.gecmis_odeme_yaz()
        self.baslangic_satir = len(self.ledger())
        self.sayfa = self.tarayici.new_page(viewport={"width": 1280, "height": 900})
        self.hatalar = []
        self.sayfa.on("pageerror", lambda e: self.hatalar.append(("pageerror", str(e))))
        self.sayfa.on("console", lambda m: self.hatalar.append((m.type, m.text)) if m.type == "error" else None)
        self.sayfa.goto(f"http://127.0.0.1:{self.port}/")
        self.sayfa.wait_for_selector("#cfoNav button[data-view=holding]")

    def tearDown(self):
        try:
            self.assertEqual(self.hatalar, [], "Tarayıcı konsolunda hata/CSP ihlali var")
        finally:
            self.sayfa.close()
            for y in reversed(self._y2):
                y.stop()
            super().tearDown()

    # --- yardimcilar ---
    def gonder(self, metin):
        self.sayfa.fill("#soru", metin)
        self.sayfa.click("#gonder")

    def son_balon(self):
        return self.sayfa.locator("#chat .message").last

    def cevap_bekle(self, metin):
        try:
            self.sayfa.locator("#chat").get_by_text(metin).last.wait_for(timeout=8000)
        except Exception:
            self.fail(f"Sohbette '{metin}' görünmedi. Sohbet:\n{self.sayfa.inner_text('#chat')[-1500:]}")

    def chip(self, etiket):
        self.sayfa.locator("#chat .cfo-chip:not([disabled])", has_text=etiket).last.click()

    # --- P5-P7: gezinme ---
    def test_holding_faaliyet_proje_tiklanir_ve_geri_calisir(self):
        s = self.sayfa
        s.click("#cfoNav button[data-view=holding]")
        s.wait_for_selector("[data-bolum=faaliyetler] tbody tr")
        s.locator("[data-bolum=faaliyetler] tbody tr", has_text="Gürsar").first.click()
        s.wait_for_selector("[data-baslik=faaliyet]")
        self.assertIn("#/faaliyet/gursar_mimarlik", s.url)
        self.assertEqual(s.inner_text("[data-baslik=faaliyet]"), "Gürsar Mimarlık")
        s.locator("[data-bolum=projeler] tbody tr", has_text="Göktürk Okulu").first.click()
        s.wait_for_selector("[data-baslik=proje]")
        self.assertEqual(s.inner_text("[data-baslik=proje]"), "Göktürk Okulu")
        govde = s.inner_text("#cfoApp")
        self.assertIn("Faaliyet: Gürsar Mimarlık", govde)
        self.assertIn("₺500.000,00", govde)                               # kazanilan gelir
        self.assertIn("Ödeme gider DEĞİLDİR", govde)                      # anlam notlari gorunur
        self.assertIn("Elektrik", govde)                                  # is kalemleri
        self.assertEqual(s.locator(".cfo-skeleton").count(), 0)            # yukleme iskeleti geride kalmaz
        # tarayici geri tusu: proje -> faaliyet -> holding
        s.go_back()
        s.wait_for_selector("[data-baslik=faaliyet]")
        s.go_back()
        s.wait_for_selector("[data-bolum=faaliyetler] tbody tr")
        # derin baglanti yenilemede korunur
        s.goto(f"http://127.0.0.1:{self.port}/#/proje/gursar_mimarlik/G%C3%B6kt%C3%BCrk%20Okulu")
        s.reload()
        s.wait_for_selector("[data-baslik=proje]")
        self.assertEqual(s.inner_text("[data-baslik=proje]"), "Göktürk Okulu")
        # kirinti: Holding'e don
        s.click(".cfo-breadcrumb a:has-text('Holding')")
        s.wait_for_selector("[data-bolum=faaliyetler] tbody tr")

    def test_projeler_menusunden_proje_acilir(self):
        s = self.sayfa
        s.click("#cfoNav button[data-view=proje]")
        s.wait_for_selector("[data-bolum=projeler] tbody tr")
        s.locator("[data-bolum=projeler] tbody tr", has_text="Göktürk").first.click()
        s.wait_for_selector("[data-baslik=proje]")
        self.assertIn("#/proje/gursar_mimarlik/", s.url)

    def test_projeden_yeni_odeme_asistani_acar(self):
        s = self.sayfa
        s.goto(f"http://127.0.0.1:{self.port}/#/proje/gursar_mimarlik/G%C3%B6kt%C3%BCrk%20Okulu")
        s.wait_for_selector("[data-baslik=proje]")
        s.click("[data-bolum=eylemler] button:has-text('Ödeme ekle')")
        self.cevap_bekle("Şimdiye kadar anladıklarım")                      # sihirbaz; faaliyet+proje tasinmis
        sohbet = s.inner_text("#chat")
        self.assertIn("Faaliyet: Gürsar Mimarlık", sohbet)
        self.assertIn("Proje: Göktürk Okulu", sohbet)
        self.assertNotIn("Hangi faaliyet", sohbet)
        self.assertNotIn("Hangi proje?", sohbet)
        self.assertEqual(len(self.ledger()), self.baslangic_satir)         # henuz yazim yok

    # --- P3-P4: sihirbaz ---
    def test_eksik_komut_sihirbazi_tutari_tekrar_sormaz(self):
        s = self.sayfa
        self.gonder("10 bin ödeme yapıldı.")
        self.cevap_bekle("Hangi faaliyet için?")
        self.chip("Gürsar Mimarlık")
        self.cevap_bekle("Hangi proje?")
        self.chip("Göktürk Okulu")
        self.cevap_bekle("iş kalemine mi, yoksa cari ödemesi mi?")
        self.chip("Cari ödemesi")
        self.cevap_bekle("Kime ödeme yapıldı?")
        self.gonder("Devrim")
        self.cevap_bekle("Onaydan önce Excel'e hiçbir şey yazılmadı")   # hesap, Devrim'in geçmişinden otomatik (belirtilerek)
        sohbet = s.inner_text("#chat")
        self.assertIn("Tutar: 10.000 TL", sohbet)
        self.assertIn("otomatik", sohbet)
        self.assertNotIn("Tutar nedir", sohbet)
        self.assertNotIn("Ne kadar", sohbet)
        self.assertEqual(len(self.ledger()), self.baslangic_satir)         # onaydan once Excel degismedi

    def test_kayit_islemler_gorunumunden_yeni_odeme(self):
        s = self.sayfa
        s.click("#cfoNav button[data-view=islemler]")
        s.click("[data-bolum=yeni-kayit] button:has-text('Yeni ödeme')")
        self.cevap_bekle("Hangi faaliyet için?")
        self.assertEqual(len(self.ledger()), self.baslangic_satir)

    def test_yeni_kayit_dugmesi_bos_sihirbazi_baslatir(self):
        self.sayfa.click(".quick-questions button:has-text('Yeni kayıt')")
        self.cevap_bekle("Hangi faaliyet için?")

    # --- §39 uctan uca senaryo ---
    def test_tam_senaryo_onay_sorgu_tekrar_ve_eksik_komut(self):
        s = self.sayfa
        # 1) tam komut -> taslak (Excel degismez)
        self.gonder(KOMUT)
        self.cevap_bekle("Onaydan önce Excel'e hiçbir şey yazılmadı")
        sohbet = s.inner_text("#chat")
        for beklenen in ("Faaliyet: Gürsar Mimarlık", "Proje: Göktürk Okulu", "Cari: Devrim", "Tutar: 10.000 TL"):
            self.assertIn(beklenen, sohbet)
        self.assertEqual(len(self.ledger()), self.baslangic_satir)         # taslak asamasinda Excel degismez
        onceki = len(self.ledger())
        # 2) tek onay -> tek Excel satiri
        s.click("#chat button.approve")
        s.fill("#approvalPassword", "x")
        s.click("#approvalPasswordForm button[type=submit]")
        self.cevap_bekle("Kayıt onaylandı")
        self.assertEqual(len(self.ledger()), onceki + 1)
        # 3) okuma sorusu: Excel degismez
        yazim_oncesi = len(self.ledger())
        self.gonder("Devrim'e toplam ne kadar ödeme yaptık?")
        self.cevap_bekle("Toplam ödeme ₺10.000,00 (1 kayıt)")              # yalniz onaylanan kayit (defter); tohum gecmis Excel-only
        self.assertEqual(len(self.ledger()), yazim_oncesi)
        # 4) ayni komut tekrar -> uyari + 'Yine de ekle' / 'Vazgeç'
        self.gonder(KOMUT)
        self.cevap_bekle("Yine de")
        self.assertEqual(len(self.ledger()), yazim_oncesi)
        self.chip("Vazgeç")
        self.cevap_bekle("Vazgeçildi")
        self.assertEqual(len(self.ledger()), yazim_oncesi)
        # 5) eksik komut: sihirbaz yalniz eksikleri sorar (tutar bilinir), secimlerle taslaga ulasir
        self.gonder("5 bin gider ekle.")
        self.cevap_bekle("Hangi faaliyet için?")
        self.chip("Gürsar Mimarlık")
        self.cevap_bekle("Hangi proje?")
        self.chip("Göktürk Okulu")
        self.cevap_bekle("Hangi iş kalemi?")
        self.chip("Elektrik")
        self.cevap_bekle("Gider hangi cariye/tedarikçiye ait?")
        self.chip("Sebahattin Çakmak")
        self.cevap_bekle("Gider Tahakkuku")                                # yalniz yeni taslak kartinda gecer
        sohbet = s.inner_text("#chat")
        self.assertIn("Tutar: 5.000 TL", sohbet)
        self.assertNotIn("Tutar nedir", sohbet)
        self.assertEqual(len(self.ledger()), yazim_oncesi)                 # onay verilmedi: Excel degismedi


if __name__ == "__main__":
    unittest.main()
