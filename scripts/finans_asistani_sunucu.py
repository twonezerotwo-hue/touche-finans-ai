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
from proje_is_kalemleri import guncelle as proje_kalemini_guncelle, listele as proje_kalemlerini_listele  # noqa: E402
from ui_belge_servisi import (  # noqa: E402
    MAX_BYTES, belgeyi_hazirla, duzelt as belge_duzelt, listele as belge_listele,
    onayla as belge_onayla, reddet as belge_reddet, yuklenen_dosyayi_kaydet,
)
from ui_yetki import eslestir, kod_uret, oturum, yetkililer, yetki_kaldir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("finans_asistani")


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
        dosya = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg"}.get(parsed.path)
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
        if parsed.path == "/api/belgeler":
            return self._json({"belgeler": belge_listele()})
        if parsed.path == "/api/proje-kalemleri":
            q = parse_qs(parsed.query)
            try:
                return self._json(proje_kalemlerini_listele((q.get("faaliyet") or ["tum"])[0]))
            except Exception as exc:
                log.exception("Proje iş kalemleri okunamadı")
                return self._json({"hata": str(exc)}, 400)
        if parsed.path == "/api/remote/users":
            if not session["yerel"]:
                return self._json({"hata": "Yalnızca ana bilgisayar."}, 403)
            return self._json({"kullanicilar": yetkililer()})
        if parsed.path == "/api/dashboard":
            q = parse_qs(parsed.query)
            faaliyet = (q.get("faaliyet") or ["tum"])[0]
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
        return self._json({"hata": "Bulunamadi"}, 404)

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
                sonuc = asistana_sor(
                    body.get("soru"), body.get("faaliyet", "tum"), body.get("profil"),
                    bool(body.get("llm", True)),
                )
                return self._json(sonuc)
            if yol == "/api/senaryo":
                body = self._body()
                return self._json(senaryo_hesapla(
                    body.get("faaliyet", "tum"), body.get("profil"), body.get("degisiklikler")
                ))
            if yol == "/api/upload":
                alanlar, dosya = self._multipart()
                hedef = yuklenen_dosyayi_kaydet(dosya[0], dosya[1])
                firma = alanlar.get("firma") or None
                return self._json(belgeyi_hazirla(hedef, firma, session["kimlik"]), 201)
            if yol == "/api/remote/code":
                if not session["yerel"]:
                    return self._json({"hata": "Yalnızca ana bilgisayar."}, 403)
                return self._json(kod_uret())
            eslesme = __import__("re").fullmatch(r"/api/belge/([a-f0-9]{24})/(onay|reddet|duzelt)", yol)
            if eslesme:
                token, eylem = eslesme.groups()
                if eylem == "onay":
                    return self._json(belge_onayla(token, session["kimlik"]))
                if eylem == "reddet":
                    return self._json(belge_reddet(token, session["kimlik"]))
                return self._json(belge_duzelt(token, self._body().get("degisiklikler"), session["kimlik"]))
            eslesme = __import__("re").fullmatch(r"/api/proje-kalemi/([A-Za-z0-9._-]{1,80})/guncelle", yol)
            if eslesme:
                return self._json(proje_kalemini_guncelle(
                    eslesme.group(1), self._body().get("degisiklikler"), session["kimlik"]
                ))
            return self._json({"hata": "Bulunamadi"}, 404)
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
