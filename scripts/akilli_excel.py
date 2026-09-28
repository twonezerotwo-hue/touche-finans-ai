# -*- coding: utf-8 -*-
"""Excel dosyalarını LLM için kaynak gösterilebilir, salt-okunur bir indekse çevirir.

excel-parser yerel olarak çalışır. SheetBrain yaklaşımındaki anla -> çalıştır ->
doğrula sırası uygulanır; model tarafından üretilen kod veya dosya yazma işlemi
çalıştırılmaz.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

from config import EXCEL_INDEX_DIR, FIRMALAR
from finans_cekirdegi import yonetim_ozeti
from ollama_client import metne_sor

try:
    from excel_parser import parse_workbook
except ImportError:  # Kurulum kontrolünde anlaşılır hata üretmek için.
    parse_workbook = None


INDEX_DIR = Path(EXCEL_INDEX_DIR)
ALLOWED_ACTIONS = frozenset({"search_chunks", "answer_with_sources"})
AYLAR = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
         "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("ı", "i")


def _tokens(value):
    return {x for x in re.findall(r"[a-z0-9]{2,}", _norm(value)) if x not in {"icin", "olan", "nedir", "hangi"}}


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _excel_yolu(firma_key):
    if firma_key not in FIRMALAR:
        raise ValueError("Faaliyet bulunamadı.")
    path = Path(FIRMALAR[firma_key]["excel"])
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError("Yalnız Excel çalışma kitapları indekslenebilir.")
    if not path.exists():
        raise FileNotFoundError(f"Excel bulunamadı: {path}")
    return path


def _cache_path(firma_key):
    if not re.fullmatch(r"[a-z0-9_]+", str(firma_key)):
        raise ValueError("Geçersiz faaliyet anahtarı.")
    return INDEX_DIR / f"{firma_key}.json"


def indeks_olustur(firma_key, zorla=False):
    """Çalışma kitabını yerel, kaynak adresli ve token-sınırlı JSON'a dönüştürür."""
    if parse_workbook is None:
        raise RuntimeError("excel-parser kurulu değil. requirements.txt bağımlılıklarını kurun.")
    excel = _excel_yolu(firma_key)
    digest = _sha256(excel)
    cache = _cache_path(firma_key)
    if not zorla and cache.exists():
        try:
            mevcut = json.loads(cache.read_text(encoding="utf-8"))
            if mevcut.get("sha256") == digest:
                return mevcut
        except (OSError, json.JSONDecodeError):
            pass

    sonuc = parse_workbook(path=str(excel))
    parcalar = []
    for chunk in sonuc.chunks:
        metin = str(chunk.render_text or "")[:16000]
        parcalar.append({
            "id": str(chunk.chunk_id),
            "source_uri": str(chunk.source_uri),
            "token_count": int(chunk.token_count or 0),
            "text": metin,
        })
    veri = {
        "schema": 1,
        "firma_key": firma_key,
        "excel": str(excel),
        "sha256": digest,
        "total_chunks": len(parcalar),
        "total_tokens": int(getattr(sonuc, "total_tokens", 0) or 0),
        "chunks": parcalar,
    }
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    gecici = cache.with_suffix(".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
    os.replace(gecici, cache)
    return veri


def ilgili_parcalar(firma_key, soru, adet=6, max_karakter=14000):
    indeks = indeks_olustur(firma_key)
    aranan = _tokens(soru)
    puanli = []
    for chunk in indeks["chunks"]:
        metin = _norm(chunk["text"] + " " + chunk["source_uri"])
        puan = sum(4 + min(6, metin.count(token)) for token in aranan if token in metin)
        if puan:
            puanli.append((puan, chunk))
    if not puanli:
        puanli = [(0, c) for c in indeks["chunks"][:adet]]
    puanli.sort(key=lambda x: (-x[0], x[1]["source_uri"]))
    secilen, toplam = [], 0
    for puan, chunk in puanli:
        metin = chunk["text"]
        if secilen and toplam + len(metin) > max_karakter:
            continue
        secilen.append({**chunk, "score": puan})
        toplam += len(metin)
        if len(secilen) >= max(1, min(int(adet), 10)):
            break
    return secilen


def plan_dogrula(plan):
    """Model planında yalnız salt-okunur, izinli eylemlere müsaade eder."""
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), list):
        raise ValueError("Geçerli bir eylem planı gerekli.")
    for action in plan["actions"]:
        if not isinstance(action, dict) or action.get("name") not in ALLOWED_ACTIONS:
            raise ValueError("İzin verilmeyen Excel eylemi.")
        if any(key in action for key in ("code", "command", "path", "formula", "write")):
            raise ValueError("Model kodu, komutu veya yazma isteği çalıştırılamaz.")
    return True


def _finansal_ozet_cevabi(firma_key, soru, excel_kaynaklari):
    normalized = _norm(soru)
    if not any(x in normalized for x in ("gelir", "gider", "net", "nakit", "banka", "borc", "alacak")):
        return None
    yil = ay = None
    iso = re.search(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])\b", normalized)
    if iso:
        yil, ay = int(iso.group(1)), int(iso.group(2))
    else:
        year = re.search(r"\b(20\d{2})\b", normalized)
        month = next((number for name, number in AYLAR.items() if name in normalized), None)
        if year and month:
            yil, ay = int(year.group(1)), month
    ozet = yonetim_ozeti(None if firma_key == "touche_holding" else firma_key, yil=yil, ay=ay)
    donem = f"{yil:04d}-{ay:02d}" if yil and ay else "tüm dönemler"
    cevap = (f"{donem} için gelir {ozet['gelir']:,.2f} TL, gider {ozet['gider']:,.2f} TL, "
             f"net sonuç {ozet['net']:,.2f} TL. Nakit/banka {ozet['nakit']:,.2f} TL, "
             f"alacak {ozet['alacak']:,.2f} TL ve borç {ozet['borc']:,.2f} TL.")
    kaynak = f"{Path(FIRMALAR[firma_key]['excel']).name}#merkezi_defter:{donem}"
    return {"cevap": cevap, "kaynaklar": [kaynak, *excel_kaynaklari[:2]], "guven": 100}


def excel_soru_cevapla(firma_key, soru):
    """Excel kanıtlarından cevap üretir ve kaynak hücre aralıklarını doğrular."""
    soru = str(soru or "").strip()
    if len(soru) < 3 or len(soru) > 1000:
        raise ValueError("Soru 3-1000 karakter arasında olmalı.")
    plan_dogrula({"actions": [{"name": "search_chunks"}, {"name": "answer_with_sources"}]})
    parcalar = ilgili_parcalar(firma_key, soru)
    kaynaklar = [x["source_uri"] for x in parcalar]
    deterministik = _finansal_ozet_cevabi(firma_key, soru, kaynaklar)
    if deterministik:
        return deterministik
    kanit = "\n\n".join(f"KAYNAK: {x['source_uri']}\n{x['text']}" for x in parcalar)
    prompt = f"""Yalnız aşağıdaki Excel kanıtlarını kullanarak Türkçe cevap ver.
Tahmin etme. Kanıt yetmiyorsa bunu açıkça belirt.
Çıktı yalnız JSON olsun: {{"cevap":"...","kaynaklar":["verilen kaynaklardan"],"guven":0}}
Kaynaklar yalnız KAYNAK satırlarında verilen tam adreslerden seçilebilir.

SORU: {soru}

KANITLAR:
{kanit}"""
    ham = metne_sor(prompt)
    cevap = str(ham.get("cevap") or ham.get("answer") or ham.get("yanit") or "").strip()
    if not cevap:
        alanlar = [(str(k), v) for k, v in ham.items() if v not in (None, "", [])][:8]
        if not alanlar:
            raise ValueError("Model kaynaklı bir cevap üretemedi.")
        cevap = "Doğrudan hesaplanmış yanıt bulunamadı. En ilgili Excel kaydı: " + ", ".join(
            f"{k}: {v}" for k, v in alanlar
        )
        ham["guven"] = 30
    dogru_kaynaklar = [x for x in ham.get("kaynaklar", []) if x in kaynaklar]
    if not dogru_kaynaklar:
        dogru_kaynaklar = kaynaklar[:2]
    try:
        guven = max(0, min(100, int(float(ham.get("guven", 0)))))
    except (TypeError, ValueError):
        guven = 0
    return {"cevap": cevap[:3000], "kaynaklar": dogru_kaynaklar[:5], "guven": guven}


def indeks_durumu(firma_key):
    cache = _cache_path(firma_key)
    if not cache.exists():
        return {"hazir": False, "firma_key": firma_key}
    try:
        veri = json.loads(cache.read_text(encoding="utf-8"))
        return {"hazir": True, "firma_key": firma_key, "sha256": veri.get("sha256"),
                "parca": veri.get("total_chunks", 0), "token": veri.get("total_tokens", 0)}
    except (OSError, json.JSONDecodeError):
        return {"hazir": False, "firma_key": firma_key}
