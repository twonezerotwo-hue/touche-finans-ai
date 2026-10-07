# -*- coding: utf-8 -*-
"""Yerel Touche Finans Asistani HTTP sunucusu."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
UI_DIR = BASE_DIR / "ui"
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))

from finans_danismani import asistana_sor, faaliyetler, karar_panosu, senaryo_hesapla  # noqa: E402
from config import MASTER_EXCEL  # noqa: E402
from kurumsal_finans import (  # noqa: E402
    banka_hareketi_eslestir, banka_mutabakat_adaylari, banka_mutabakat_listesi,
    butce_sapma_raporu, cari_yaslandirma, donem_kapanis_kontrolu,
    hesap_eslemesi_kaydet, hesap_eslemelerini_listele, konsolide_mizan,
    kur_degerleme, kur_kaydet, kurlari_listele, kurumsal_durum,
    onay_kurali_kaydet, onay_kurallarini_listele, sirketler_arasi_mutabakat,
    sirketler_arasi_transfer,
)
from proje_is_kalemleri import guncelle as proje_kalemini_guncelle, listele as proje_kalemlerini_listele  # noqa: E402
from ui_belge_servisi import (  # noqa: E402
    MAX_BYTES, belgeyi_hazirla, duzelt as belge_duzelt, listele as belge_listele,
    onayla as belge_onayla, reddet as belge_reddet, sohbet_taslagi_duzelt,
    sohbet_taslagi_hazirla, yuklenen_dosyayi_kaydet, _onay_parolasini_dogrula,
    ozet as belge_ozet,
)
from sohbet_kaydi import _norm as sohbet_norm, faaliyet_key_bul, kayit_niyeti_mi  # noqa: E402
from ui_yetki import eslestir, kod_uret, oturum, yetkililer, yetki_kaldir  # noqa: E402

# === CFO Operating System katmanlari (additive) ===
import audit_db  # noqa: E402
import fpa_ileri  # noqa: E402
import yetki as rol_yetki  # noqa: E402
import veri_katmani  # noqa: E402
import ai_cfo  # noqa: E402
import raporlar  # noqa: E402
from ai_cfo import insights_uret  # noqa: E402
from belge_pipeline import kuyruk_durumu, kuyruga_ekle, pipeline_calistir  # noqa: E402
from belge_pipeline import icerik_hash as belge_icerik_hash  # noqa: E402
from belge_pipeline import mukerrer_kontrol as belge_mukerrer_kontrol  # noqa: E402
from butce_forecast import butce_gerceklesen, taahhut_ozeti  # noqa: E402
from finans_motoru import boyut_kirilimi, holding_kpi, is_kalemi_grid, proje_karlilik  # noqa: E402
from fx_provider import kur_cevir  # noqa: E402
from money import tl_metin  # noqa: E402
from nakit_13hafta import nakit_13_hafta  # noqa: E402
from senaryo_motoru import (  # noqa: E402
    senaryo_hesapla as senaryo_motoru_hesapla, senaryo_karsilastir,
    senaryo_kaydet, senaryolari_listele,
)
from ticari import alacaklar, borclar, yaklasan_odemeler  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("finans_asistani")


def _ekran_kpi(faaliyet, profil):
    """Tüm ekranlarda karar panosunun tek likidite/ödeme hesabını kullanır."""
    kpi = holding_kpi(faaliyet, profil)
    pano = karar_panosu(faaliyet or "tum", {
        "rezerv_gun": int((profil or {}).get("rezerv_gun", 30) or 30),
        "risk": "orta", "yatirim_vadesi_ay": 12,
    })
    ozet, plan = pano["ozet"], pano["nakit_plani"]
    nakit = round(float(ozet["nakit"]) * 100)
    korunacak = round(float(plan["rezerv"]) * 100)
    beklenen = round(float(plan["30_gun_beklenen_tahsilat"]) * 100)
    odeme = round(float(plan["30_gun_vadeli_odeme"]) * 100)
    kapasite = round(float(plan["yatirim_kapasitesi"]) * 100)
    kapanislar = [round(float(x["kapanis"]) * 100) for x in pano.get("nakit_projeksiyonu", [])]
    kpi.update({
        "toplam_nakit_kurus": nakit,
        "korunacak_nakit_kurus": korunacak,
        "kullanilabilir_nakit_kurus": nakit - korunacak,
        "toplam_alacak_kurus": round(float(ozet["alacak"]) * 100),
        "toplam_borc_kurus": round(float(ozet["borc"]) * 100),
        "yatirim_kapasitesi_kurus": kapasite,
        "beklenen_tahsilat_30_kurus": beklenen,
        "vadesi_gelen_odeme_30_kurus": odeme,
        "min_13_hafta_nakit_kurus": min(kapanislar) if kapanislar else nakit,
    })
    kpi["metin"].update({
        "toplam_nakit": tl_metin(nakit / 100),
        "kullanilabilir_nakit": tl_metin((nakit - korunacak) / 100),
        "korunacak_nakit": tl_metin(korunacak / 100),
        "toplam_alacak": tl_metin(ozet["alacak"]),
        "toplam_borc": tl_metin(ozet["borc"]),
        "yatirim_kapasitesi": tl_metin(kapasite / 100),
        "min_13_hafta": tl_metin((min(kapanislar) if kapanislar else nakit) / 100),
    })
    return kpi


def _sohbet_taslak_cevabi(taslak, duzeltildi=False):
    kayitlar = taslak.get("kayitlar") or []
    dogrulama = taslak.get("dogrulama") or {}
    eksikler = taslak.get("eksik_alanlar") or []
    if dogrulama.get("durum") == "FAIL":
        giris = "Taslağı güncelledim ancak kayıt için eksik veya geçersiz bilgi var."
    else:
        giris = "Kayıt taslağını güncelledim." if duzeltildi else "Kayıt taslağını hazırladım."
    satirlar = []
    for sira, kayit in enumerate(kayitlar[:10], 1):
        satirlar.append(
            f"\n{sira}) {kayit.get('tarih') or 'Tarih eksik'} · "
            f"{kayit.get('islem') or 'İşlem türü eksik'} · {kayit.get('cari') or 'Cari eksik'} · "
            f"{kayit.get('tutar') if kayit.get('tutar') not in (None, '') else 'Tutar eksik'} {kayit.get('pb') or 'TL'}"
            f"\n   {kayit.get('faaliyet') or taslak.get('firma') or 'Faaliyet eksik'} · {kayit.get('proje') or 'Proje yok'}"
            + (f" · Yeni cari: {kayit.get('cari_tipi') or 'tip eksik'}" if kayit.get("yeni_cari") else "")
            + (f" · Vade: {kayit.get('vade')}" if kayit.get("vade") else "")
        )
        otomatik = kayit.get("otomatik_eslesmeler") or []
        if otomatik:
            satirlar.append("\n   Otomatik eşleşme: " + " | ".join(otomatik[:3]))
    detay = "".join(satirlar) or "\nKayıt üretilemedi."
    notlar = (dogrulama.get("hatalar", []) + dogrulama.get("uyarilar", []))[:6]
    if notlar:
        detay += "\nKontrol: " + " | ".join(str(x) for x in notlar)
    son = "\nBilgiler doğruysa Onayla'ya basın ve Excel onay şifresini girin. Onaydan önce Excel'e hiçbir şey yazılmadı."
    if dogrulama.get("durum") == "FAIL":
        son = "\nBu taslak henüz onaylanamaz. Eksik veya yanlış bilgiyi doğal dille yazın."
    if eksikler:
        son = (
            "\nŞu eksik bilgileri tek mesajda yazın:\n- " + "\n- ".join(eksikler)
            + "\nÖrnek cevap: ‘Tarih 06.10.2026, proje Göktürk Okulu, hesap Gürsar TL Kasa’."
        )
    return {
        "cevap": giris + detay + son,
        "niyet": "kayit_eksikleri_bekliyor" if eksikler else "kayit_taslagi",
        "guven": dogrulama.get("skor", 0),
        "llm": taslak.get("motor") or "deterministik", "kayit_taslagi": taslak,
        "kanitlar": [f"Doğrulama: {dogrulama.get('durum', 'FAIL')} ({dogrulama.get('skor', 0)}/100)"],
        "aksiyonlar": [], "degisiklik_yapildi": False,
    }


def _son_bekleyen_sohbet_taslagi():
    return next(
        (x for x in belge_listele("sohbet") if x.get("durum") == "ONAY_BEKLIYOR"),
        None,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "ToucheFinans/1.0"

    def _headers(self, status=200, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, veri, status=200):
        govde = json.dumps(veri, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(govde)

    def _excel_indir(self):
        yol = Path(MASTER_EXCEL)
        if not yol.exists():
            return self._json({"hata": "Ana Excel bulunamadı."}, 404)
        govde = yol.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", 'attachment; filename="Touche-Holding-Finans.xlsx"')
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(govde)

    def _body(self):
        uzunluk = int(self.headers.get("Content-Length", "0") or 0)
        if uzunluk < 1 or uzunluk > 65536:
            raise ValueError("Gecersiz istek boyutu.")
        return json.loads(self.rfile.read(uzunluk).decode("utf-8"))

    def _oturum(self):
        return oturum(self.headers, self.headers.get("Host", ""), self.client_address[0])

    def _yetki_gerekli(self):
        session = self._oturum()
        if not session["yetkili"]:
            self._json({"hata": "Bu cihaz henüz yetkilendirilmedi.", "oturum": session}, 403)
            return None
        return session

    def _yerel_gerekli(self):
        session = self._oturum()
        if not session["yetkili"] or not session["yerel"]:
            self._json({"hata": "Bu işlem yalnızca ana bilgisayardan yapılabilir."}, 403)
            return None
        return session

    def _origin_gecerli(self, session):
        if session.get("yerel"):
            return True
        origin = self.headers.get("Origin", "")
        if not origin:
            return False
        return urlparse(origin).netloc.casefold() == self.headers.get("Host", "").casefold()

    def _rol(self, session) -> str:
        """Oturumdan rol turetir (yerel yonetici = OWNER)."""
        if session.get("rol"):
            return rol_yetki.rol_normalize(session["rol"])
        return "OWNER" if session.get("yerel") else "FINANCE_MANAGER"

    def _faaliyet_param(self, q) -> str | None:
        deger = (q.get("faaliyet") or [None])[0]
        return None if deger in {None, "", "tum", "touche_holding"} else deger

    def _faaliyet_body(self, body) -> str | None:
        deger = str((body or {}).get("faaliyet") or "").strip()
        return None if deger in {"", "tum", "touche_holding"} else deger

    def _multipart(self):
        uzunluk = int(self.headers.get("Content-Length", "0") or 0)
        if uzunluk < 1 or uzunluk > MAX_BYTES + 1024 * 1024:
            raise ValueError("Geçersiz dosya boyutu.")
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("multipart/form-data gerekli.")
        ham = self.rfile.read(uzunluk)
        mesaj = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + ham
        )
        alanlar, dosya = {}, None
        for parca in mesaj.iter_parts():
            ad = parca.get_param("name", header="content-disposition")
            dosya_adi = parca.get_filename()
            icerik = parca.get_payload(decode=True) or b""
            if dosya_adi and ad == "dosya":
                dosya = (dosya_adi, icerik)
            elif ad:
                alanlar[ad] = icerik.decode("utf-8", errors="replace").strip()
        if not dosya:
            raise ValueError("Dosya alanı bulunamadı.")
        return alanlar, dosya

    def do_GET(self):
        parsed = urlparse(self.path)
        dosya = {
            "/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg",
            "/cfo.js": "cfo.js", "/cfo.css": "cfo.css", "/i18n.js": "i18n.js",
        }.get(parsed.path)
        if dosya:
            yol = UI_DIR / dosya
            if not yol.exists():
                return self._json({"hata": "Arayuz dosyasi eksik"}, 404)
            tip = mimetypes.guess_type(yol.name)[0] or "application/octet-stream"
            self._headers(200, f"{tip}; charset=utf-8" if tip.startswith("text/") or tip.endswith("javascript") else tip)
            self.wfile.write(yol.read_bytes())
            return
        if parsed.path == "/api/health":
            return self._json({"durum": "ok", "yerel": True})
        if parsed.path == "/api/session":
            return self._json(self._oturum())
        session = self._yetki_gerekli()
        if not session:
            return
        if parsed.path == "/api/faaliyetler":
            return self._json({"faaliyetler": faaliyetler()})
        if parsed.path == "/api/excel/bilgi":
            yol = Path(MASTER_EXCEL)
            return self._json({
                "ad": yol.name, "yol": str(yol) if session.get("yerel") else "Ana bilgisayardaki tek master Excel",
                "mevcut": yol.exists(), "boyut": yol.stat().st_size if yol.exists() else 0,
                "yerel": bool(session.get("yerel")),
            })
        if parsed.path == "/api/excel/indir":
            return self._excel_indir()
        if parsed.path == "/api/belgeler":
            return self._json({"belgeler": belge_listele()})
        if parsed.path == "/api/sohbet-taslaklari":
            return self._json({"taslaklar": belge_listele("sohbet")})
        if parsed.path == "/api/proje-kalemleri":
            q = parse_qs(parsed.query)
            if not self._kapsam_izinli(session, self._faaliyet_param(q)):
                return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
            try:
                return self._json(proje_kalemlerini_listele((q.get("faaliyet") or ["tum"])[0]))
            except Exception as exc:
                log.exception("Proje iş kalemleri okunamadı")
                return self._json({"hata": str(exc)}, 400)
        if parsed.path == "/api/kurumsal":
            q = parse_qs(parsed.query)
            faaliyet = (q.get("faaliyet") or [None])[0]
            if faaliyet in {"tum", "touche_holding", ""}:
                faaliyet = None
            if not self._kapsam_izinli(session, faaliyet):
                return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
            return self._json(kurumsal_durum(faaliyet))
        if parsed.path == "/api/cari-yaslandirma":
            q = parse_qs(parsed.query)
            faaliyet = (q.get("faaliyet") or [None])[0]
            if faaliyet in {"tum", "touche_holding", ""}:
                faaliyet = None
            if not self._kapsam_izinli(session, faaliyet):
                return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
            return self._json({"kalemler": cari_yaslandirma(
                (q.get("hesap") or ["120"])[0], faaliyet, (q.get("tarih") or [None])[0]
            )})
        if parsed.path == "/api/konsolide-mizan":
            q = parse_qs(parsed.query)
            if not self._kapsam_izinli(session, None):
                return self._json({"hata": "Konsolide rapor için yetkiniz yok."}, 403)
            return self._json({"kalemler": konsolide_mizan(
                (q.get("baslangic") or ["2000-01-01"])[0],
                (q.get("bitis") or ["2099-12-31"])[0], True,
            )})
        if parsed.path == "/api/sirketler-arasi":
            q = parse_qs(parsed.query)
            if not self._kapsam_izinli(session, None):
                return self._json({"hata": "Konsolide rapor için yetkiniz yok."}, 403)
            return self._json({"islemler": sirketler_arasi_mutabakat(
                (q.get("baslangic") or [None])[0], (q.get("bitis") or [None])[0]
            )})
        if parsed.path == "/api/hesap-eslemeleri":
            q = parse_qs(parsed.query)
            if not self._kapsam_izinli(session, self._faaliyet_param(q)):
                return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
            return self._json({"eslemeler": hesap_eslemelerini_listele((q.get("faaliyet") or [None])[0])})
        if parsed.path == "/api/kurlar":
            return self._json({"kurlar": kurlari_listele()})
        if parsed.path == "/api/onay-kurallari":
            return self._json({"kurallar": onay_kurallarini_listele()})
        if parsed.path == "/api/banka-mutabakat":
            q = parse_qs(parsed.query)
            return self._json({"hareketler": banka_mutabakat_listesi((q.get("durum") or [None])[0])})
        if parsed.path == "/api/banka-adaylari":
            q = parse_qs(parsed.query)
            return self._json({"adaylar": banka_mutabakat_adaylari((q.get("hareket_id") or [""])[0])})
        if parsed.path == "/api/donem-kontrol":
            q = parse_qs(parsed.query)
            return self._json(donem_kapanis_kontrolu(
                int((q.get("yil") or [0])[0]), int((q.get("ay") or [0])[0])
            ))
        if parsed.path == "/api/butce-sapma":
            q = parse_qs(parsed.query)
            if not self._kapsam_izinli(session, self._faaliyet_param(q)):
                return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
            return self._json(butce_sapma_raporu(
                (q.get("faaliyet") or [""])[0], (q.get("baslangic") or [""])[0],
                (q.get("bitis") or [""])[0], (q.get("proje") or [None])[0],
            ))
        if parsed.path == "/api/remote/users":
            if not session["yerel"]:
                return self._json({"hata": "Yalnızca ana bilgisayar."}, 403)
            return self._json({"kullanicilar": yetkililer()})
        if parsed.path == "/api/dashboard":
            q = parse_qs(parsed.query)
            faaliyet = (q.get("faaliyet") or ["tum"])[0]
            if not self._kapsam_izinli(session, None if faaliyet in {"tum", "touche_holding", ""} else faaliyet):
                return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
            profil = {
                "risk": (q.get("risk") or ["dusuk"])[0],
                "rezerv_gun": (q.get("rezerv_gun") or [30])[0],
                "yatirim_vadesi_ay": (q.get("yatirim_vadesi_ay") or [3])[0],
            }
            try:
                return self._json(karar_panosu(faaliyet, profil))
            except Exception as exc:
                log.exception("Dashboard hatasi")
                return self._json({"hata": str(exc)}, 400)
        try:
            cevap = self._cfo_get(parsed.path, parse_qs(parsed.query), session)
        except PermissionError as exc:
            return self._json({"hata": str(exc)}, 403)
        except Exception as exc:
            log.exception("CFO ucu hatasi")
            return self._json({"hata": str(exc)}, 400)
        if cevap is not None:
            return self._json(cevap)
        return self._json({"hata": "Bulunamadi"}, 404)

    def _kapsam_izinli(self, session, faaliyet) -> bool:
        """Rol kapsaminda istenen faaliyet/proje izinli mi?

        Kapsam '*' ise tum holding; aksi halde yalnizca listelenen faaliyetler.
        Kapsamli kullanicilar icin konsolide (tum) erisim kapalidir.
        """
        kapsam = session.get("faaliyetler")
        if not kapsam or "*" in kapsam:
            return True
        if faaliyet in {None, "", "tum", "touche_holding"}:
            return False
        return faaliyet in kapsam

    def _faaliyet_q(self, q, session):
        faaliyet = self._faaliyet_param(q)
        if not self._kapsam_izinli(session, faaliyet):
            raise PermissionError("Bu faaliyet/proje için yetkiniz yok.")
        return faaliyet

    def _cfo_get(self, path, q, session):
        """CFO Operating System GET uclari; eslesme yoksa None doner."""
        if path == "/api/kpi":
            return _ekran_kpi(self._faaliyet_q(q, session), self._body_profil(q))
        if path == "/api/kirilim":
            return {"kalemler": boyut_kirilimi((q.get("boyut") or ["sirket"])[0], self._faaliyet_q(q, session))}
        if path == "/api/proje-karlilik":
            return {"kalemler": proje_karlilik(self._faaliyet_q(q, session))}
        if path == "/api/is-kalemleri-karlilik":
            return {"kalemler": is_kalemi_grid(self._faaliyet_q(q, session), (q.get("proje") or [None])[0])}
        if path == "/api/ticari/alacak":
            return alacaklar(self._faaliyet_q(q, session), (q.get("tarih") or [None])[0])
        if path == "/api/ticari/borc":
            return borclar(self._faaliyet_q(q, session), (q.get("tarih") or [None])[0])
        if path == "/api/ticari/yaklasan":
            return {"odemeler": yaklasan_odemeler(self._faaliyet_q(q, session), gun=int((q.get("gun") or [30])[0]))}
        if path == "/api/nakit/13hafta":
            return nakit_13_hafta(self._faaliyet_q(q, session))
        if path == "/api/butce/gerceklesen":
            return butce_gerceklesen(self._faaliyet_q(q, session) or "tum", (q.get("baslangic") or [None])[0],
                                     (q.get("bitis") or [None])[0], (q.get("proje") or [None])[0])
        if path == "/api/taahhut":
            return taahhut_ozeti(self._faaliyet_q(q, session))
        if path == "/api/ai-cfo/insights":
            return {"insights": insights_uret(self._faaliyet_q(q, session))}
        if path == "/api/ai-cfo/baglam":
            return ai_cfo.cfo_baglam(self._faaliyet_q(q, session))
        if path == "/api/rapor":
            ad = (q.get("ad") or [""])[0]
            kapsam = {"faaliyet": self._faaliyet_q(q, session)}
            if ad in {"budget-vs-actual"}:
                kapsam.update({"baslangic": (q.get("baslangic") or [None])[0],
                               "bitis": (q.get("bitis") or [None])[0],
                               "proje": (q.get("proje") or [None])[0]})
            return raporlar.rapor_getir(ad, **kapsam)
        if path == "/api/fpa/duyarlilik":
            return fpa_ileri.duyarlilik_analizi(self._faaliyet_q(q, session))
        if path == "/api/fpa/basabas":
            return fpa_ileri.basabas(self._faaliyet_q(q, session))
        if path == "/api/fpa/monte-carlo":
            return fpa_ileri.monte_carlo(self._faaliyet_q(q, session),
                                         iterasyon=int((q.get("iterasyon") or [1000])[0]))
        if path == "/api/audit":
            if not rol_yetki.izin_var(self._rol(session), "audit.goruntule"):
                raise PermissionError("Yetkisiz.")
            return {"kayitlar": audit_db.kayitlar(limit=int((q.get("limit") or [100])[0]))}
        if path == "/api/yetki/roller":
            return {"roller": rol_yetki.rol_listesi(), "aktif_rol": self._rol(session),
                    "kapsam": session.get("faaliyetler")}
        if path == "/api/fpa/dogruluk":
            return fpa_ileri.dogruluk_ozeti()
        if path == "/api/belge/kuyruk":
            return kuyruk_durumu((q.get("belge_id") or [""])[0])
        if path == "/api/fx/cevir":
            return kur_cevir((q.get("tutar") or ["0"])[0], (q.get("kaynak") or ["USD"])[0],
                             (q.get("hedef") or ["TL"])[0], (q.get("tarih") or [None])[0])
        if path == "/api/senaryolar":
            return {"senaryolar": senaryolari_listele()}
        if path == "/api/senaryo/karsilastir":
            return senaryo_karsilastir()
        if path == "/api/raporlar":
            return {"raporlar": raporlar.rapor_listesi()}
        return None

    def _body_profil(self, q):
        return {"rezerv_gun": int((q.get("rezerv_gun") or [30])[0]),
                "hedef_marj": float((q.get("hedef_marj") or [15])[0])}

    def do_POST(self):
        yol = urlparse(self.path).path
        if yol == "/api/session/pair":
            try:
                session = self._oturum()
                if not session.get("kimlik") or session["yerel"]:
                    return self._json({"hata": "Eşleştirme yalnızca Tailscale üzerinden yapılabilir."}, 403)
                if not self._origin_gecerli(session):
                    return self._json({"hata": "İstek kaynağı doğrulanamadı."}, 403)
                kimlik = eslestir(self._body().get("kod"), session["kimlik"])
                return self._json({"basari": True, "kimlik": kimlik})
            except Exception as exc:
                return self._json({"hata": str(exc)}, 400)
        session = self._yetki_gerekli()
        if not session:
            return
        if not self._origin_gecerli(session):
            return self._json({"hata": "İstek kaynağı doğrulanamadı."}, 403)
        try:
            if yol == "/api/chat":
                body = self._body()
                soru = str(body.get("soru") or "").strip()
                aktif_taslak = str(body.get("aktif_taslak") or "").strip()
                sade = sohbet_norm(soru)
                onceki_niyet = str(body.get("onceki_niyet") or "").strip()
                onceki_soru = str(body.get("onceki_soru") or "").strip()
                if sade in {
                    "bekleyen kayit taslagini goster", "bekleyen taslagi goster",
                    "taslagi goster", "bekleyen kayitlari goster",
                }:
                    taslak = _son_bekleyen_sohbet_taslagi()
                    if taslak:
                        return self._json(_sohbet_taslak_cevabi(taslak))
                    return self._json({
                        "cevap": "Onay bekleyen sohbet taslağı bulunmuyor.",
                        "niyet": "bekleyen_taslak_yok", "guven": 100, "llm": "deterministik",
                        "kanitlar": [], "aksiyonlar": [], "degisiklik_yapildi": False,
                    })
                if aktif_taslak and sade in {"onayla", "kaydet", "evet onayla"}:
                    return self._json({
                        "cevap": "Excel değişikliğini onaylamak için açılan pencerede onay şifresini girin.",
                        "niyet": "kayit_onayi", "guven": 100, "llm": "deterministik",
                        "parola_gerekli": True, "kayit_taslagi_token": aktif_taslak,
                        "degisiklik_yapildi": False, "kanitlar": [], "aksiyonlar": [],
                    })
                if aktif_taslak and sade in {"iptal", "reddet", "vazgec", "vazgectim"}:
                    reddedilen = belge_reddet(aktif_taslak, session.get("kimlik") or "yerel_kullanici")
                    return self._json({
                        "cevap": "Taslak iptal edildi. Excel'e hiçbir kayıt yazılmadı.",
                        "niyet": "kayit_reddi", "guven": 100, "llm": "deterministik",
                        "taslak_kapat": True, "degisiklik_yapildi": False,
                        "kanitlar": [f"Taslak: {reddedilen['belge_id']}"], "aksiyonlar": [],
                    })
                if aktif_taslak and sade.startswith("duzelt"):
                    taslak = sohbet_taslagi_duzelt(
                        aktif_taslak, soru, session.get("kimlik") or "yerel_kullanici"
                    )
                    return self._json(_sohbet_taslak_cevabi(taslak, duzeltildi=True))
                if aktif_taslak and onceki_niyet == "kayit_eksikleri_bekliyor":
                    mevcut = belge_ozet(aktif_taslak)
                    if mevcut.get("durum") == "ONAY_BEKLIYOR" and mevcut.get("eksik_alanlar"):
                        taslak = sohbet_taslagi_duzelt(
                            aktif_taslak, f"Eksik bilgileri tamamla: {soru}",
                            session.get("kimlik") or "yerel_kullanici",
                        )
                        return self._json(_sohbet_taslak_cevabi(taslak, duzeltildi=True))
                if onceki_niyet == "kayit_faaliyeti_bekliyor":
                    if sade in {"iptal", "vazgec", "vazgectim"}:
                        return self._json({
                            "cevap": "Kayıt talebi iptal edildi. Excel'e hiçbir şey yazılmadı.",
                            "niyet": "kayit_reddi", "guven": 100, "llm": "deterministik",
                            "taslak_kapat": True, "degisiklik_yapildi": False,
                            "kanitlar": [], "aksiyonlar": [],
                        })
                    firma_key = faaliyet_key_bul(soru, body.get("faaliyet", "tum"))
                    if firma_key and onceki_soru:
                        birlesik_talep = f"{onceki_soru}\nFaaliyet: {soru}"
                        taslak = sohbet_taslagi_hazirla(
                            birlesik_talep, firma_key, session.get("kimlik") or "yerel_kullanici"
                        )
                        return self._json(_sohbet_taslak_cevabi(taslak))
                    return self._json({
                        "cevap": "Faaliyeti tanıyamadım. Örneğin yalnızca ‘Gürsar Mimarlık’ yazın. Henüz Excel'e yazmadım.",
                        "niyet": "kayit_faaliyeti_bekliyor", "guven": 100, "llm": "deterministik",
                        "baglam_sorusu": onceki_soru, "kanitlar": [], "aksiyonlar": [],
                        "degisiklik_yapildi": False,
                    })
                if kayit_niyeti_mi(soru):
                    firma_key = faaliyet_key_bul(soru, body.get("faaliyet", "tum"))
                    if not firma_key:
                        return self._json({
                            "cevap": "Bu kaydın hangi faaliyete ait olduğunu yazın. Örneğin: ‘Gürsar Mimarlık için …’. Henüz Excel'e yazmadım.",
                            "niyet": "kayit_faaliyeti_bekliyor", "guven": 100, "llm": "deterministik",
                            "baglam_sorusu": soru, "kanitlar": [], "aksiyonlar": [], "degisiklik_yapildi": False,
                        })
                    taslak = sohbet_taslagi_hazirla(
                        soru, firma_key, session.get("kimlik") or "yerel_kullanici"
                    )
                    return self._json(_sohbet_taslak_cevabi(taslak))
                excel_degisikligi_onayli = False
                if body.get("parola"):
                    _onay_parolasini_dogrula(
                        body.get("parola"), session.get("kimlik") or "yerel_kullanici"
                    )
                    excel_degisikligi_onayli = True
                sonuc = asistana_sor(
                    soru, body.get("faaliyet", "tum"), body.get("profil"),
                    bool(body.get("llm", True)), session.get("kimlik") or "yerel_kullanici",
                    body.get("onceki_niyet"), body.get("onceki_soru"),
                    degisiklik_onayli=excel_degisikligi_onayli,
                )
                return self._json(sonuc)
            if yol == "/api/excel/ac":
                if not session.get("yerel"):
                    return self._json({"hata": "Excel yalnızca ana bilgisayarda doğrudan açılabilir."}, 403)
                excel_yolu = Path(MASTER_EXCEL)
                if not excel_yolu.exists():
                    raise FileNotFoundError("Ana Excel bulunamadı.")
                os.startfile(str(excel_yolu))
                return self._json({"basari": True, "mesaj": "Ana Excel bilgisayarda açıldı."})
            if yol == "/api/senaryo":
                body = self._body()
                return self._json(senaryo_hesapla(
                    body.get("faaliyet", "tum"), body.get("profil"), body.get("degisiklikler")
                ))
            if yol == "/api/upload":
                rol_yetki.kontrol(self._rol(session), "belge.yukle")
                alanlar, dosya = self._multipart()
                firma = alanlar.get("firma") or None
                if not self._kapsam_izinli(session, firma):
                    return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
                hedef = yuklenen_dosyayi_kaydet(dosya[0], dosya[1])
                # Deterministik mukerrer kontrol (belge hatti upload rotasina bagli)
                dup = belge_mukerrer_kontrol(belge_icerik_hash(dosya_yolu=hedef))
                sonuc = belgeyi_hazirla(hedef, firma, session["kimlik"])
                if dup["mukerrer"]:
                    sonuc["mukerrer_uyari"] = dup
                audit_db.kaydet("DOCUMENT_UPLOADED", actor=session.get("kimlik"),
                                entity_type="belge", entity_id=sonuc.get("belge_id"),
                                after={"firma": firma, "dosya": dosya[0], "mukerrer": dup["mukerrer"]})
                return self._json(sonuc, 201)
            if yol == "/api/remote/code":
                if not session["yerel"]:
                    return self._json({"hata": "Yalnızca ana bilgisayar."}, 403)
                return self._json(kod_uret())
            if yol == "/api/sirketler-arasi/transfer":
                rol_yetki.kontrol(self._rol(session), "transfer.yap")
                body = self._body()
                if not (self._kapsam_izinli(session, body.get("kaynak"))
                        and self._kapsam_izinli(session, body.get("hedef"))):
                    return self._json({"hata": "Transfer kapsamınız dışında."}, 403)
                sonuc = sirketler_arasi_transfer(
                    body.get("kaynak"), body.get("hedef"), body.get("tarih"), body.get("tutar"),
                    body.get("aciklama"), session["kimlik"], body.get("proje", ""),
                    body.get("para_birimi", "TL"), body.get("kur", 1),
                )
                audit_db.kaydet("INTERCOMPANY_TRANSFER", actor=session.get("kimlik"),
                                entity_type="sirketler_arasi", entity_id=sonuc["islem_id"],
                                after={"kaynak": body.get("kaynak"), "hedef": body.get("hedef"),
                                       "tutar": body.get("tutar")})
                return self._json(sonuc, 201)
            if yol == "/api/hesap-esleme":
                rol_yetki.kontrol(self._rol(session), "ayar.duzenle")
                body = self._body()
                if not self._kapsam_izinli(session, body.get("faaliyet")):
                    return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
                return self._json(hesap_eslemesi_kaydet(
                    body.get("faaliyet"), body.get("yerel_hesap"), body.get("grup_hesap"),
                    session["kimlik"], body.get("aciklama", ""),
                ))
            if yol == "/api/kur":
                rol_yetki.kontrol(self._rol(session), "kur.duzenle")
                body = self._body()
                sonuc = kur_kaydet(
                    body.get("tarih"), body.get("para_birimi"), body.get("kur"),
                    session["kimlik"], body.get("kaynak", "manuel"),
                )
                audit_db.kaydet("FX_RATE_SET", actor=session.get("kimlik"), entity_type="kur",
                                entity_id=f"{body.get('tarih')}:{body.get('para_birimi')}",
                                after={"kur": body.get("kur")})
                return self._json(sonuc, 201)
            if yol == "/api/kur-degerleme":
                rol_yetki.kontrol(self._rol(session), "kur.duzenle")
                body = self._body()
                sonuc = kur_degerleme(body.get("tarih"), session["kimlik"])
                audit_db.kaydet("FX_REVALUATION", actor=session.get("kimlik"),
                                entity_type="donem", entity_id=str(body.get("tarih")), after=sonuc)
                return self._json(sonuc)
            if yol == "/api/onay-kurali":
                rol_yetki.kontrol(self._rol(session), "ayar.duzenle")
                body = self._body()
                onay_kurali_kaydet(
                    body.get("kural_id"), body.get("faaliyet", "*"), body.get("min_tutar", 0),
                    body.get("max_tutar"), body.get("gerekli_onay", 1), body.get("roller"),
                    bool(body.get("ayri_hazirlayan")), session["kimlik"],
                )
                return self._json({"basari": True}, 201)
            if yol == "/api/banka-eslestir":
                rol_yetki.kontrol(self._rol(session), "finans.duzenle")
                body = self._body()
                return self._json(banka_hareketi_eslestir(
                    body.get("hareket_id"), body.get("fis_id"), session["kimlik"],
                    bool(body.get("zorla", False)),
                ))
            eslesme = __import__("re").fullmatch(r"/api/belge/([a-f0-9]{24})/(onay|reddet|duzelt)", yol)
            if eslesme:
                rol_yetki.kontrol(self._rol(session), "belge.onayla")
                token, eylem = eslesme.groups()
                if eylem == "onay":
                    sonuc = belge_onayla(token, session["kimlik"], self._body().get("parola"))
                    audit_db.kaydet("DOCUMENT_APPROVED", actor=session.get("kimlik"),
                                    entity_type="belge", entity_id=token)
                    return self._json(sonuc)
                if eylem == "reddet":
                    sonuc = belge_reddet(token, session["kimlik"])
                    audit_db.kaydet("DOCUMENT_REJECTED", actor=session.get("kimlik"),
                                    entity_type="belge", entity_id=token)
                    return self._json(sonuc)
                return self._json(belge_duzelt(token, self._body().get("degisiklikler"), session["kimlik"]))
            eslesme = __import__("re").fullmatch(r"/api/proje-kalemi/([A-Za-z0-9._-]{1,80})/guncelle", yol)
            if eslesme:
                rol_yetki.kontrol(self._rol(session), "finans.duzenle")
                body = self._body()
                _onay_parolasini_dogrula(body.get("parola"), session["kimlik"])
                return self._json(proje_kalemini_guncelle(
                    eslesme.group(1), body.get("degisiklikler"), session["kimlik"]
                ))
            if yol == "/api/senaryo/motor":
                rol_yetki.kontrol(self._rol(session), "finans.goruntule")
                body = self._body()
                if not self._kapsam_izinli(session, self._faaliyet_body(body)):
                    return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
                return self._json(senaryo_motoru_hesapla(
                    body.get("faaliyet", "tum"), body.get("profil"), body.get("degisiklikler")))
            if yol == "/api/senaryo/kaydet":
                rol_yetki.kontrol(self._rol(session), "senaryo.olustur")
                body = self._body()
                if not self._kapsam_izinli(session, self._faaliyet_body(body)):
                    return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
                sonuc = senaryo_kaydet(body.get("ad"), body.get("tip", "BASE"),
                                       body.get("faaliyet", "tum"), body.get("profil"),
                                       body.get("degisiklikler"), session.get("kimlik") or "yerel")
                audit_db.kaydet("SCENARIO_CREATED", actor=session.get("kimlik"),
                                entity_type="senaryo", entity_id=sonuc["senaryo_id"],
                                after={"ad": sonuc["ad"], "tip": sonuc["tip"]})
                return self._json(sonuc, 201)
            if yol == "/api/ai-cfo/sor":
                rol_yetki.kontrol(self._rol(session), "ai.sor")
                body = self._body()
                if not self._kapsam_izinli(session, self._faaliyet_body(body)):
                    return self._json({"hata": "Bu faaliyet için yetkiniz yok."}, 403)
                return self._json(ai_cfo.soru_cevapla(body.get("soru", ""), body.get("faaliyet")))
            if yol == "/api/belge/pipeline":
                rol_yetki.kontrol(self._rol(session), "belge.yukle")
                body = self._body()
                sonuc = pipeline_calistir(body.get("metin", ""), body.get("dosya_adi", ""),
                                          body.get("firma"), body.get("proje"))
                if body.get("belge_id"):
                    kuyruga_ekle(body.get("belge_id"), sonuc, session.get("kimlik") or "yerel")
                    sonuc["kuyruk"] = {"belge_id": body.get("belge_id"), "durum": "USER_REVIEW"}
                audit_db.kaydet("DOCUMENT_CLASSIFIED", actor=session.get("kimlik"),
                                entity_type="belge", entity_id=body.get("belge_id"),
                                after={"tur": sonuc["tur"], "guven": sonuc["guven"]})
                return self._json(sonuc, 201)
            if yol == "/api/fpa/dogruluk-kaydet":
                rol_yetki.kontrol(self._rol(session), "finans.duzenle")
                body = self._body()
                return self._json(fpa_ileri.dogruluk_kaydet(
                    body.get("donem"), body.get("onceki_tahmin"), body.get("gerceklesen"),
                    body.get("olcu", "NAKIT")), 201)
            if yol == "/api/demo/hierarchy":
                rol_yetki.kontrol(self._rol(session), "ayar.duzenle")
                return self._json(veri_katmani.demo_holding_hierarchy_yukle(), 201)
            return self._json({"hata": "Bulunamadi"}, 404)
        except PermissionError as exc:
            return self._json({"hata": str(exc)}, 403)
        except Exception as exc:
            log.exception("Asistan hatasi")
            return self._json({"hata": str(exc)}, 400)

    def do_DELETE(self):
        session = self._yerel_gerekli()
        if not session:
            return
        if not self._origin_gecerli(session):
            return self._json({"hata": "İstek kaynağı doğrulanamadı."}, 403)
        yol = urlparse(self.path).path
        if yol.startswith("/api/remote/users/"):
            from urllib.parse import unquote
            yetki_kaldir(unquote(yol.rsplit("/", 1)[-1]))
            return self._json({"basari": True})
        return self._json({"hata": "Bulunamadi"}, 404)

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


def calistir(host="127.0.0.1", port=None):
    port = int(port or os.environ.get("FINANS_UI_PORT", "8765"))
    sunucu = ThreadingHTTPServer((host, port), Handler)
    print(f"Touche Finans Asistani: http://{host}:{port}", flush=True)
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        sunucu.server_close()


if __name__ == "__main__":
    calistir()
