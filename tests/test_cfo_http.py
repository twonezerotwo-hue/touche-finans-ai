# -*- coding: utf-8 -*-
"""HTTP duzeyinde CFO akisi: sihirbaz, sorgu, gezinme veri uclari, yetki/kapsam, sifre ile onay."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import finans_cekirdegi as cekirdek
import ui_yetki
from cfo_test_temeli import CfoTestTemeli


class CfoHttpTestleri(CfoTestTemeli):
    @classmethod
    def setUpClass(cls):
        import finans_asistani_sunucu as srv

        cls.srv = srv
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        self._y2 = [patch.object(ui_yetki, "YETKI_DOSYASI", self.root / "ui_yetkililer.json"),
                    patch.object(ui_yetki, "ROLLER_DOSYASI", self.root / "ui_roller.json")]
        for y in self._y2:
            y.start()

    def tearDown(self):
        for y in reversed(self._y2):
            y.stop()
        super().tearDown()

    def req(self, method, yol, body=None, kimlik=None):
        if kimlik:
            basliklar = {"Host": "pc.tailnet.ts.net", "Origin": "http://pc.tailnet.ts.net", "Tailscale-User-Login": kimlik}
        else:
            basliklar = {}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        if data is not None:
            basliklar["Content-Type"] = "application/json"
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{yol}", data=data, method=method, headers=basliklar)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")

    def chat(self, soru, **ek):
        return self.req("POST", "/api/chat", {"soru": soru, "faaliyet": "tum", "llm": False, **ek})

    # --- tam akis: dogal dil -> sihirbaz -> sifreli onay -> Excel ---
    def test_http_tam_komut_taslak_onay_excel(self):
        self.gecmis_odeme_yaz()
        st, d = self.chat("Göktürk okuluna Gürsar Mimarlık adına Devrim'e 10 bin TL ödeme yapıldı.")
        self.assertEqual(st, 200, d)
        self.assertEqual(d["niyet"], "cfo_sihirbaz")
        self.assertEqual(d["llm"], "deterministik")
        self.assertIsNotNone(d["kayit_taslagi"], d["cevap"])
        self.assertIn("Faaliyet: Gürsar Mimarlık", d["cevap"])
        self.assertIn("Tutar: 10.000 TL", d["cevap"])
        self.assertEqual(len(self.ledger()), 1)                    # sadece geçmiş kayıt; yenisi yazılmadı
        token = d["kayit_taslagi"]["token"]
        st, onay = self.req("POST", f"/api/belge/{token}/onay", {"parola": "x"})
        self.assertEqual(st, 200, onay)
        self.assertEqual(len(self.ledger()), 2)

    def test_http_eksik_komut_dugme_ve_yazi_cevaplariyla_ilerler(self):
        st, d = self.chat("10 bin ödeme yapıldı.")
        self.assertEqual(d["cfo"]["soru"]["alan"], "faaliyet")
        tid = d["cfo"]["taslak_id"]
        st, d = self.req("POST", "/api/cfo/adim", {"taslak_id": tid, "alan": "faaliyet", "secim": "gursar_mimarlik"})
        self.assertEqual(d["cfo"]["soru"]["alan"], "proje")
        st, d = self.chat("göktürk", cfo_taslak=tid)                              # yazılı cevap
        self.assertEqual(d["cfo"]["soru"]["alan"], "islem")
        st, d = self.req("POST", "/api/cfo/adim", {"taslak_id": tid, "alan": "islem", "secim": "COUNTERPARTY_PAYMENT"})
        st, d = self.chat("Devrim", cfo_taslak=tid)
        self.assertEqual(d["cfo"]["soru"]["alan"], "hesap")
        st, d = self.req("POST", "/api/cfo/adim", {"taslak_id": tid, "alan": "hesap", "secim": "Gürsar TL Banka"})
        self.assertIsNotNone(d["kayit_taslagi"])
        self.assertEqual(d["kayit_taslagi"]["kayitlar"][0]["tutar"], 10000.0)

    def test_http_sorgu_okuma_yapar_excel_degismez(self):
        import hashlib

        cekirdek.finans_kaydi_yaz({"belge_id": "O1", "firma_key": "gursar_mimarlik", "tur": "finans_belgesi",
                                   "veri": {"kayitlar": [{"Tarih": "2026-09-20", "Islem_Turu": "Usta/Tedarikçi Ödemesi",
                                                          "Tutar": 100_000, "PB": "TL", "Kur": 1, "Cari": "Devrim",
                                                          "Proje": "Göktürk Okulu", "Hesap_Kasa": "Gürsar TL Banka"}]}}, "t")
        once = hashlib.sha256(self.ana.read_bytes()).hexdigest()
        st, d = self.chat("Göktürk okulunda ne kadar ödeme yaptık?")
        self.assertEqual(st, 200)
        self.assertEqual(d["niyet"], "cfo_sorgu")
        self.assertIn("100.000,00", d["cevap"])
        self.assertFalse(d["degisiklik_yapildi"])
        self.assertEqual(hashlib.sha256(self.ana.read_bytes()).hexdigest(), once)
        self.assertEqual(ui_taslak_sayisi(), 0)
        st, d = self.chat("Göktürk okuluna ait bütün Devrim ödemelerini yaz")
        self.assertEqual(st, 200)
        self.assertEqual(d["niyet"], "cfo_sorgu")
        self.assertIn("100.000,00", d["cevap"])
        self.assertIsNone(d.get("kayit_taslagi"))
        self.assertEqual(ui_taslak_sayisi(), 0)

    def test_http_ayni_komut_ikinci_kez_engellenir(self):
        self.gecmis_odeme_yaz()
        _, d = self.chat("Göktürk okuluna Gürsar Mimarlık adına Devrim'e 10 bin TL ödeme yapıldı.")
        self.req("POST", f"/api/belge/{d['kayit_taslagi']['token']}/onay", {"parola": "x"})
        _, d2 = self.chat("Göktürk okuluna Gürsar Mimarlık adına Devrim'e 10 bin TL ödeme yapıldı.")
        self.assertEqual(d2["niyet"], "cfo_tekrar_uyarisi")
        self.assertEqual(len(self.ledger()), 2)

    def test_http_desteklenmeyen_islem_aciklama_verir(self):
        _, d = self.chat("Gürsar'dan Holding'e 100 bin TL grup içi transfer yaz")
        self.assertEqual(d["niyet"], "cfo_desteklenmiyor")
        self.assertEqual(self.ledger(), [])

    def test_http_bos_baslangic_yeni_odeme_her_seyi_sirayla_sorar(self):
        st, d = self.chat("yeni ödeme")
        self.assertEqual(st, 200)
        self.assertEqual(d["niyet"], "cfo_sihirbaz")
        self.assertEqual(d["cfo"]["soru"]["alan"], "faaliyet")
        self.assertEqual(ui_taslak_sayisi(), 0)
        for soru in ("yeni kayıt", "Yeni Gider", "kayıt ekle"):
            st, d = self.chat(soru)
            self.assertEqual(d["niyet"], "cfo_sihirbaz", soru)
            self.assertEqual(d["cfo"]["soru"]["alan"], "faaliyet", soru)

    def test_http_tekrar_yine_de_ve_vazgec(self):
        self.gecmis_odeme_yaz()
        komut = "Göktürk okuluna Gürsar Mimarlık adına Devrim'e 10 bin TL ödeme yapıldı."
        _, d = self.chat(komut)
        self.req("POST", f"/api/belge/{d['kayit_taslagi']['token']}/onay", {"parola": "x"})
        _, d2 = self.chat(komut)
        self.assertEqual(d2["niyet"], "cfo_tekrar_uyarisi")
        # vazgeç: hiçbir şey yazılmaz, taslak yok
        st, v = self.req("POST", "/api/cfo/adim", {"alan": "tekrar", "secim": "vazgec", "metin": komut})
        self.assertEqual(st, 200)
        self.assertEqual(v["niyet"], "kayit_reddi")
        self.assertEqual(len(self.ledger()), 2)
        # yine de ekle: yeni taslak açılır; onaydan sonra ikinci kayıt yazılır
        st, y = self.req("POST", "/api/cfo/adim", {"alan": "tekrar", "secim": "yine_de", "metin": komut})
        self.assertEqual(st, 200, y)
        self.assertIsNotNone(y["kayit_taslagi"])
        self.req("POST", f"/api/belge/{y['kayit_taslagi']['token']}/onay", {"parola": "x"})
        self.assertEqual(len(self.ledger()), 3)

    def test_http_sihirbaz_iptal(self):
        _, d = self.chat("10 bin ödeme yapıldı")
        tid = d["cfo"]["taslak_id"]
        st, i = self.chat("iptal", cfo_taslak=tid)
        self.assertEqual(st, 200)
        self.assertEqual(ui_taslak_sayisi(), 0)
        self.assertFalse(i.get("degisiklik_yapildi"))

    # --- gezinme veri uclari ---
    def test_http_holding_faaliyet_proje_uclari(self):
        cekirdek.finans_kaydi_yaz({"belge_id": "H1", "firma_key": "gursar_mimarlik", "tur": "finans_belgesi",
                                   "veri": {"kayitlar": [{"Tarih": "2026-09-01", "Islem_Turu": "Hakediş / Gelir Tahakkuku",
                                                          "Tutar": 500_000, "PB": "TL", "Kur": 1, "Cari": "İşveren",
                                                          "Proje": "Göktürk Okulu"}]}}, "t")
        st, h = self.req("GET", "/api/holding-ozeti")
        self.assertEqual(st, 200)
        self.assertEqual({f["faaliyet_key"] for f in h["faaliyetler"]}, {"touche_holding", "gursar_mimarlik"})
        st, f = self.req("GET", "/api/faaliyet-detay?faaliyet=gursar_mimarlik")
        self.assertEqual(f["faaliyet"], "Gürsar Mimarlık")
        self.assertEqual(f["metrikler"]["kazanilan_gelir"]["deger_kurus"], 500_000 * 100)
        st, l = self.req("GET", "/api/proje-listesi?faaliyet=gursar_mimarlik")
        self.assertEqual({p["proje"] for p in l["projeler"]}, {"Göktürk Okulu", "Bahçeşehir Villa"})
        st, p = self.req("GET", "/api/proje-detay?faaliyet=gursar_mimarlik&proje=G%C3%B6kt%C3%BCrk%20Okulu")
        self.assertEqual(p["proje"], "Göktürk Okulu")
        self.assertEqual(p["metrikler"]["kazanilan_gelir"]["deger_kurus"], 500_000 * 100)
        self.assertEqual(p["metrikler"]["tahsil_edilen"]["deger_kurus"], 0)       # tahsilat != gelir
        st, e = self.req("GET", "/api/proje-detay?faaliyet=gursar_mimarlik&proje=Yok")
        self.assertEqual(st, 400)

    # --- yetki ve kapsam ---
    def _kimlik(self, kimlik, rol, faaliyetler):
        ui_yetki._kaydet(ui_yetki._yukle() | {kimlik})
        ui_yetki.rol_ata(kimlik, rol, faaliyetler)

    def test_http_viewer_taslak_olusturamaz(self):
        self._kimlik("v@x", "VIEWER", ["*"])
        st, d = self.req("POST", "/api/chat", {"soru": "Devrim'e 10 bin TL ödeme yapıldı", "faaliyet": "tum"}, "v@x")
        self.assertEqual(d["niyet"], "yetkisiz")
        self.assertEqual(ui_taslak_sayisi(), 0)
        # ama sorgu (okuma) serbest
        st, d = self.req("POST", "/api/chat", {"soru": "Devrim'e toplam ne kadar ödeme yapıldı?", "faaliyet": "tum"}, "v@x")
        self.assertEqual(d["niyet"], "cfo_sorgu")

    def test_http_kapsamli_kullanici_baska_faaliyeti_goremez_secemez(self):
        self._kimlik("pm@x", "PROJECT_MANAGER", ["gursar_mimarlik"])
        for yol in ("/api/holding-ozeti", "/api/faaliyet-detay?faaliyet=touche_holding"):
            st, _ = self.req("GET", yol, kimlik="pm@x")
            self.assertEqual(st, 403, yol)
        st, d = self.req("GET", "/api/faaliyet-detay?faaliyet=gursar_mimarlik", kimlik="pm@x")
        self.assertEqual(st, 200)
        st, d = self.req("GET", "/api/proje-listesi", kimlik="pm@x")
        self.assertEqual(st, 200)
        self.assertTrue(all(p["faaliyet_key"] == "gursar_mimarlik" for p in d["projeler"]))
        # sihirbaz faaliyet seçeneklerinde yalnız kendi kapsamı
        st, d = self.req("POST", "/api/chat", {"soru": "10 bin ödeme yapıldı", "faaliyet": "tum"}, "pm@x")
        self.assertEqual([s["id"] for s in d["cfo"]["soru"]["secenekler"]], ["gursar_mimarlik"])

    def test_http_baska_kullanici_taslagini_degistiremez(self):
        self._kimlik("a@x", "ACCOUNTANT", ["*"])
        self._kimlik("b@x", "ACCOUNTANT", ["*"])
        _, d = self.req("POST", "/api/chat", {"soru": "10 bin ödeme yapıldı", "faaliyet": "tum"}, "a@x")
        tid = d["cfo"]["taslak_id"]
        st, hata = self.req("POST", "/api/cfo/adim", {"taslak_id": tid, "alan": "faaliyet", "secim": "gursar_mimarlik"}, "b@x")
        self.assertEqual(st, 403)

    def test_baslangic_ozeti_tek_ana_yedek_ve_kaynak_agacini_yazar(self):
        satirlar = self.srv.baslangic_ozeti("127.0.0.1", 8765)
        metin = "\n".join(satirlar)
        self.assertIn("http://127.0.0.1:8765", metin)
        self.assertIn(f"SMART_EXCEL_MAIN_WORKBOOK={self.ana}", metin)
        self.assertIn(f"SMART_EXCEL_BACKUP_WORKBOOK={self.yedek}", metin)
        self.assertTrue(any(x.startswith("SMART_EXCEL_ROOT=") for x in satirlar))
        self.assertTrue(any(x.startswith("SMART_EXCEL_CODE=") for x in satirlar))

    def test_http_excel_durum_yedek_ve_ana(self):
        self.gecmis_odeme_yaz()
        st, d = self.req("GET", "/api/excel/durum")
        self.assertEqual(st, 200)
        self.assertTrue(d["ana"]["var"] and d["yedek"]["var"])
        self.assertEqual(d["gecici_dosya_sayisi"], 0)
        st, b = self.req("GET", "/api/excel/bilgi")
        self.assertTrue(b["yedek_mevcut"])

    def test_http_master_excel_aktarimi_sadece_owner_ve_cfo(self):
        self._kimlik("viewer-export@x", "VIEWER", ["*"])
        st, hata = self.req("GET", "/api/excel/indir", kimlik="viewer-export@x")
        self.assertEqual(st, 403)
        self.assertIn("OWNER/CFO", hata["hata"])

        self._kimlik("cfo-export@x", "CFO", ["*"])
        istek = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/excel/indir",
            method="GET",
            headers={
                "Host": "pc.tailnet.ts.net",
                "Origin": "http://pc.tailnet.ts.net",
                "Tailscale-User-Login": "cfo-export@x",
            },
        )
        with urllib.request.urlopen(istek, timeout=30) as cevap:
            self.assertEqual(cevap.status, 200)
            self.assertTrue(cevap.read(4))

    def test_http_belge_taslagi_sahibi_disinda_gorunmez_ve_onaylanamaz(self):
        self._kimlik("owner-draft@x", "ACCOUNTANT", ["gursar_mimarlik"])
        st, taslak = self.req(
            "POST", "/api/chat",
            {"soru": "Gürsar Mimarlık Göktürk Okulu projesinde Devrim'e 10 bin TL Gürsar TL Banka hesabından ödeme yapıldı", "faaliyet": "gursar_mimarlik"},
            kimlik="owner-draft@x",
        )
        self.assertEqual(st, 200, taslak)
        token = taslak["kayit_taslagi"]["token"]
        self._kimlik("other-draft@x", "CFO", ["touche_holding"])
        st, liste = self.req("GET", "/api/sohbet-taslaklari", kimlik="other-draft@x")
        self.assertEqual(st, 200)
        self.assertEqual(liste["taslaklar"], [])
        st, hata = self.req("POST", f"/api/belge/{token}/onay", {"parola": "x"}, kimlik="other-draft@x")
        self.assertEqual(st, 403)
        self.assertIn("kapsamınızda değil", hata["hata"])


def ui_taslak_sayisi():
    import ui_belge_servisi as ui

    return len(ui.listele("sohbet"))


if __name__ == "__main__":
    unittest.main()
