# -*- coding: utf-8 -*-
"""invoice2data icin guvenli ve sinirli entegrasyon katmani.

Bu katman yalnizca yerel YAML sablonlariyla eslesen metin tabanli PDF
faturalarini okur. Eslesme veya zorunlu alanlar eksikse karar vermez; cagiran
kod mevcut Qwen akisina geri doner.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from config import INVOICE2DATA_ETKIN, INVOICE2DATA_SABLON_DIR

log = logging.getLogger(__name__)

DESTEKLENEN_AKISLAR = {("touche_holding", "fatura")}


def _tarih(deger):
    if isinstance(deger, (datetime, date)):
        return deger.strftime("%d.%m.%Y")
    return str(deger or "").strip()


def _ilk(sozluk, *anahtarlar):
    for anahtar in anahtarlar:
        deger = sozluk.get(anahtar)
        if deger not in (None, ""):
            return deger
    return ""


def _sayi(deger):
    if isinstance(deger, (int, float)):
        return float(deger)
    metin = str(deger or "").strip().replace(" ", "").replace("₺", "")
    if not metin:
        return None
    if "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        return float(metin)
    except (TypeError, ValueError):
        return None


def _sonucu_donustur(ham):
    """invoice2data sonucunu mevcut Touche fatura semasina donusturur."""
    if not isinstance(ham, dict) or not ham:
        return None

    tedarikci = str(_ilk(ham, "partner_name", "supplier_name", "supplier", "issuer")).strip()
    if not tedarikci or tedarikci == "Türk e-Fatura":
        return None
    tarih = _tarih(_ilk(ham, "date", "invoice_date"))
    fatura_no = str(_ilk(ham, "invoice_number", "invoice_no")).strip()
    vkn = str(_ilk(ham, "supplier_vkn", "vat_id", "tax_id", "vat_number")).strip()
    para = str(_ilk(ham, "currency") or "TRY").upper()
    toplam = _sayi(_ilk(ham, "amount", "total", "total_amount"))
    kdv_toplam = _sayi(_ilk(ham, "amount_tax", "vat", "tax", "tax_amount"))
    satirlar = _ilk(ham, "lines", "line_items", "items")
    satirlar = satirlar if isinstance(satirlar, list) else []

    kayitlar = []
    for satir in satirlar:
        if not isinstance(satir, dict):
            continue
        urun = str(_ilk(satir, "description", "name", "product", "item")).strip()
        miktar = _sayi(_ilk(satir, "quantity", "qty"))
        birim_fiyat = _sayi(_ilk(satir, "unit_price", "price_unit", "price"))
        satir_toplam = _sayi(_ilk(satir, "amount", "price_subtotal", "line_total", "total"))
        if not urun or miktar is None or birim_fiyat is None or satir_toplam is None:
            continue
        kayitlar.append({
            "Tarih": tarih,
            "Fatura_No": fatura_no,
            "Tedarikci": tedarikci,
            "VKN": vkn,
            "Urun": urun,
            "Miktar": miktar,
            "Birim_Fiyat": birim_fiyat,
            "KDV_Orani": _sayi(_ilk(satir, "tax_rate", "taxpercent", "vat_rate")) or "",
            "KDV_Tutar": _sayi(_ilk(satir, "tax_amount", "amount_tax", "vat")) or "",
            "Toplam": satir_toplam,
            "PB": "TL" if para in {"TRY", "TRL", "TL"} else para,
        })

    ozet_kaydi = False
    if not kayitlar and toplam is not None:
        # Kalemsiz e-faturalarda finansal toplami kaybetmeden tek ozet satiri
        # olustururuz. Bu kayit ana akista zorunlu REVIEW durumuna cekilir.
        ozet_kaydi = True
        kayitlar.append({
            "Tarih": tarih,
            "Fatura_No": fatura_no,
            "Tedarikci": tedarikci,
            "VKN": vkn,
            "Urun": "Fatura toplamı",
            "Miktar": 1,
            "Birim_Fiyat": toplam,
            "KDV_Orani": "",
            "KDV_Tutar": kdv_toplam if kdv_toplam is not None else "",
            "Toplam": toplam,
            "PB": "TL" if para in {"TRY", "TRL", "TL"} else para,
        })

    if not kayitlar or not tarih or not fatura_no:
        return None
    return {
        "kayitlar": kayitlar,
        "_cikarim": {
            "motor": "invoice2data",
            "sablon": str(ham.get("template_name", "")),
            "ozet_kaydi": ozet_kaydi,
        },
    }


def durum():
    """Kurulum ve yerel sablon durumunu yan etkisiz olarak raporlar."""
    sablon_dir = Path(INVOICE2DATA_SABLON_DIR)
    sablon_sayisi = len(list(sablon_dir.glob("*.yml"))) + len(list(sablon_dir.glob("*.yaml")))
    try:
        import invoice2data  # noqa: F401
        from invoice2data.input import pdfium
        kurulu = True
        pdf_okuyucu = bool(pdfium.is_available())
    except Exception:
        kurulu = False
        pdf_okuyucu = False
    return {
        "etkin": bool(INVOICE2DATA_ETKIN),
        "kurulu": kurulu,
        "pdf_okuyucu": pdf_okuyucu,
        "sablon_sayisi": sablon_sayisi,
        "sablon_dir": str(sablon_dir),
    }


def belgeyi_oku(dosya_yolu, firma_key, tur):
    """Eslesen faturayi okur; basarisizlikta Qwen'e gecis icin sebep dondurur."""
    yol = Path(dosya_yolu)
    if not INVOICE2DATA_ETKIN:
        return {"eslesti": False, "sebep": "devre_disi"}
    if (firma_key, tur) not in DESTEKLENEN_AKISLAR:
        return {"eslesti": False, "sebep": "akis_desteklenmiyor"}
    if yol.suffix.lower() != ".pdf":
        return {"eslesti": False, "sebep": "yalnizca_metin_pdf"}

    sablon_dir = Path(INVOICE2DATA_SABLON_DIR)
    try:
        from invoice2data import extract_data
        from invoice2data.extract.loader import read_templates
        from invoice2data.input import pdfium

        sablonlar = read_templates(str(sablon_dir))
        if not sablonlar:
            return {"eslesti": False, "sebep": "sablon_yok"}
        if not pdfium.is_available():
            return {"eslesti": False, "sebep": "pdf_okuyucu_yok"}
        ham = extract_data(
            str(yol),
            templates=sablonlar,
            input_module=pdfium,
            ai_fallback=False,
            raise_on_error=False,
        )
        veri = _sonucu_donustur(ham)
        if not veri:
            return {"eslesti": False, "sebep": "eslesme_veya_alan_yok"}
        return {
            "eslesti": True,
            "veri": veri,
            "sablon": veri["_cikarim"]["sablon"],
            "ozet_kaydi": veri["_cikarim"]["ozet_kaydi"],
        }
    except Exception as exc:
        log.warning("invoice2data okuma hatasi: %s", exc)
        return {"eslesti": False, "sebep": "okuma_hatasi", "hata": str(exc)[:300]}
