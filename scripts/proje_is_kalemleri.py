# -*- coding: utf-8 -*-
"""Tek master Excel'deki proje iş kalemlerini güvenli biçimde yönetir."""

from __future__ import annotations

import math
import os
import shutil
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from openpyxl import load_workbook

from config import FIRMALAR
from excel_yazici import _gecici_exceli_dogrula, _gecici_yol, _yedek_al
from finans_cekirdegi import _baglanti, faaliyet_anahtari_bul, veritabani_yedekle
from guvenlik import audit_yaz
from kayit_kilidi import kilitli


SAYFA = "06_Proje İş Kalemleri"
ILK_SATIR = 6
SON_SATIR = 500

ALANLAR = {
    "metraj": "G",
    "satis_birim_fiyati": "H",
    "maliyet_turu": "J",
    "maliyet_birim_fiyati": "K",
    "durum": "O",
    "not": "P",
}
MALIYET_TURLERI = {"Birim fiyat", "Toplam sabit"}
DURUMLAR = {"Onaylı", "Taslak", "İptal"}


def _excel_yolu() -> Path:
    return Path(FIRMALAR["touche_holding"]["excel"])


def _norm(deger) -> str:
    import unicodedata

    metin = unicodedata.normalize("NFKD", str(deger or ""))
    return "".join(c for c in metin if not unicodedata.combining(c)).casefold().replace("ı", "i").strip()


def _sayi(deger, alan: str) -> float:
    if isinstance(deger, bool):
        raise ValueError(f"{alan} sayı olmalı.")
    metin = str(deger if deger is not None else "").strip().replace("₺", "").replace(" ", "")
    if "," in metin and "." in metin:
        metin = metin.replace(".", "").replace(",", ".")
    elif "," in metin:
        metin = metin.replace(",", ".")
    try:
        sonuc = float(Decimal(metin))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{alan} geçerli bir sayı olmalı.") from exc
    if not math.isfinite(sonuc) or sonuc < 0 or sonuc > 1_000_000_000_000:
        raise ValueError(f"{alan} 0 ile 1 trilyon arasında olmalı.")
    return sonuc


def _tutar_kurus(deger: float) -> int:
    return int((Decimal(str(deger)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _satir_verisi(ws, satir: int) -> dict:
    metraj = float(ws[f"G{satir}"].value or 0)
    satis_bf = float(ws[f"H{satir}"].value or 0)
    maliyet_turu = str(ws[f"J{satir}"].value or "Birim fiyat").strip()
    maliyet_bf = float(ws[f"K{satir}"].value or 0)
    ciro = metraj * satis_bf
    maliyet = maliyet_bf if _norm(maliyet_turu) == "toplam sabit" else metraj * maliyet_bf
    fark = ciro - maliyet
    return {
        "kalem_id": str(ws[f"A{satir}"].value or "").strip(),
        "faaliyet": str(ws[f"B{satir}"].value or "").strip(),
        "faaliyet_key": faaliyet_anahtari_bul(ws[f"B{satir}"].value),
        "proje": str(ws[f"C{satir}"].value or "").strip(),
        "maliyet_grubu": str(ws[f"D{satir}"].value or "").strip(),
        "is_kalemi": str(ws[f"E{satir}"].value or "").strip(),
        "birim": str(ws[f"F{satir}"].value or "").strip(),
        "metraj": metraj,
        "satis_birim_fiyati": satis_bf,
        "ciro": round(ciro, 2),
        "maliyet_turu": maliyet_turu,
        "maliyet_birim_fiyati": maliyet_bf,
        "maliyet": round(maliyet, 2),
        "brut_fark": round(fark, 2),
        "brut_marj": round(fark / ciro * 100, 2) if ciro else 0,
        "durum": str(ws[f"O{satir}"].value or "").strip(),
        "not": str(ws[f"P{satir}"].value or "").strip(),
        "hakedis_kayit_id": str(ws[f"Q{satir}"].value or "").strip(),
        "gider_kayit_id": str(ws[f"R{satir}"].value or "").strip(),
    }


def _kalemler(ws) -> list[dict]:
    sonuc = []
    for satir in range(ILK_SATIR, SON_SATIR + 1):
        if str(ws[f"A{satir}"].value or "").strip():
            sonuc.append(_satir_verisi(ws, satir))
    return sonuc


def _ozet(kalemler: list[dict]) -> dict:
    onayli = [x for x in kalemler if _norm(x["durum"]) == "onayli"]
    ciro = round(sum(x["ciro"] for x in onayli), 2)
    maliyet = round(sum(x["maliyet"] for x in onayli), 2)
    fark = round(ciro - maliyet, 2)
    return {
        "kalem_sayisi": len(kalemler),
        "onayli_kalem_sayisi": len(onayli),
        "ciro": ciro,
        "maliyet": maliyet,
        "brut_fark": fark,
        "brut_marj": round(fark / ciro * 100, 2) if ciro else 0,
    }


def listele(faaliyet: str = "tum") -> dict:
    yol = _excel_yolu()
    if not yol.exists():
        raise FileNotFoundError(f"Ana Excel bulunamadı: {yol}")
    wb = load_workbook(yol, read_only=True, data_only=False)
    try:
        if SAYFA not in wb.sheetnames:
            raise ValueError(f"Ana Excel'de {SAYFA} sayfası bulunamadı.")
        kalemler = _kalemler(wb[SAYFA])
    finally:
        wb.close()
    if faaliyet not in {"tum", "touche_holding"}:
        kalemler = [x for x in kalemler if x["faaliyet_key"] == faaliyet]
    return {"kalemler": kalemler, "ozet": _ozet(kalemler), "kaynak": str(yol)}


def _bagli_toplamlar(ws) -> dict[str, float]:
    toplamlar: dict[str, float] = {}
    for kalem in _kalemler(ws):
        if kalem["hakedis_kayit_id"]:
            toplamlar.setdefault(kalem["hakedis_kayit_id"], 0)
        if kalem["gider_kayit_id"]:
            toplamlar.setdefault(kalem["gider_kayit_id"], 0)
        if _norm(kalem["durum"]) != "onayli":
            continue
        if kalem["hakedis_kayit_id"]:
            toplamlar[kalem["hakedis_kayit_id"]] = toplamlar.get(kalem["hakedis_kayit_id"], 0) + kalem["ciro"]
        if kalem["gider_kayit_id"]:
            toplamlar[kalem["gider_kayit_id"]] = toplamlar.get(kalem["gider_kayit_id"], 0) + kalem["maliyet"]
    return {key: round(value, 2) for key, value in toplamlar.items()}


def _defteri_senkronla(toplamlar: dict[str, float]) -> None:
    with _baglanti() as con:
        for kayit_id, tutar in toplamlar.items():
            belge_id = f"LEGACY-{kayit_id}"
            satirlar = con.execute(
                "SELECT s.fis_id,s.satir_no,s.hesap_kodu,s.borc_kurus,s.alacak_kurus,h.hesap_tipi "
                "FROM fis_satirlari s JOIN fisler f ON f.fis_id=s.fis_id "
                "JOIN hesaplar h ON h.kod=s.hesap_kodu WHERE f.belge_id=? ORDER BY s.fis_id,s.satir_no",
                (belge_id,),
            ).fetchall()
            if len(satirlar) != 2 or len({x["fis_id"] for x in satirlar}) != 1:
                raise ValueError(f"{kayit_id} için merkez defter eşleşmesi eksik veya belirsiz.")
            kurus = _tutar_kurus(tutar)
            for satir in satirlar:
                borc_tarafi = satir["borc_kurus"] > 0 or (
                    satir["borc_kurus"] == satir["alacak_kurus"] == 0
                    and satir["hesap_tipi"] in {"VARLIK", "GIDER"}
                )
                con.execute(
                    "UPDATE fis_satirlari SET borc_kurus=?,alacak_kurus=? WHERE fis_id=? AND satir_no=?",
                    (kurus if borc_tarafi else 0, 0 if borc_tarafi else kurus, satir["fis_id"], satir["satir_no"]),
                )
            kontrol = con.execute(
                "SELECT SUM(borc_kurus) b,SUM(alacak_kurus) a FROM fis_satirlari WHERE fis_id=?",
                (satirlar[0]["fis_id"],),
            ).fetchone()
            if kontrol["b"] != kontrol["a"]:
                raise RuntimeError(f"{kayit_id} güncellemesi merkez defteri dengesiz bıraktı.")


def _geri_al(yedek: Path, hedef: Path) -> None:
    gecici = _gecici_yol(hedef)
    try:
        shutil.copy2(yedek, gecici)
        _gecici_exceli_dogrula(gecici, ["03_Kayıt Merkezi", SAYFA])
        os.replace(gecici, hedef)
    finally:
        gecici.unlink(missing_ok=True)


@kilitli
def guncelle(kalem_id: str, degisiklikler: dict, aktor: str) -> dict:
    kalem_id = str(kalem_id or "").strip()
    if not kalem_id or not isinstance(degisiklikler, dict) or not degisiklikler:
        raise ValueError("Kalem kimliği ve en az bir değişiklik gerekli.")
    bilinmeyen = set(degisiklikler) - set(ALANLAR)
    if bilinmeyen:
        raise ValueError(f"Değiştirilemeyen alan: {', '.join(sorted(bilinmeyen))}")

    temiz = {}
    for alan, deger in degisiklikler.items():
        if alan in {"metraj", "satis_birim_fiyati", "maliyet_birim_fiyati"}:
            temiz[alan] = _sayi(deger, alan)
        elif alan == "maliyet_turu":
            if deger not in MALIYET_TURLERI:
                raise ValueError("Maliyet türü Birim fiyat veya Toplam sabit olmalı.")
            temiz[alan] = deger
        elif alan == "durum":
            if deger not in DURUMLAR:
                raise ValueError("Durum Onaylı, Taslak veya İptal olmalı.")
            temiz[alan] = deger
        else:
            temiz[alan] = str(deger or "").strip()[:500]

    yol = _excel_yolu()
    wb = load_workbook(yol, data_only=False, keep_links=True)
    gecici = None
    excel_yedek = db_yedek = None
    try:
        if SAYFA not in wb.sheetnames:
            raise ValueError(f"Ana Excel'de {SAYFA} sayfası bulunamadı.")
        ws = wb[SAYFA]
        hedef_satir = next((r for r in range(ILK_SATIR, SON_SATIR + 1) if str(ws[f"A{r}"].value or "").strip() == kalem_id), None)
        if not hedef_satir:
            raise ValueError(f"İş kalemi bulunamadı: {kalem_id}")
        once = _satir_verisi(ws, hedef_satir)
        for alan, deger in temiz.items():
            ws[f"{ALANLAR[alan]}{hedef_satir}"] = deger
        sonra = _satir_verisi(ws, hedef_satir)
        faaliyet_ozeti = _ozet([x for x in _kalemler(ws) if x["faaliyet_key"] == sonra["faaliyet_key"]])
        toplamlar = _bagli_toplamlar(ws)
        if hasattr(wb, "calculation"):
            wb.calculation.calcMode = "auto"
            wb.calculation.fullCalcOnLoad = True
            wb.calculation.forceFullCalc = True

        excel_yedek = _yedek_al("touche_holding")
        db_yedek = veritabani_yedekle()
        gecici = _gecici_yol(yol)
        wb.save(gecici)
        wb.close()
        wb = None
        _gecici_exceli_dogrula(gecici, ["03_Kayıt Merkezi", SAYFA])
        os.replace(gecici, yol)
        gecici = None
        try:
            _defteri_senkronla(toplamlar)
        except Exception:
            _geri_al(excel_yedek, yol)
            raise
    finally:
        if wb is not None:
            wb.close()
        if gecici is not None:
            Path(gecici).unlink(missing_ok=True)

    audit_yaz(
        "proje_is_kalemi_guncellendi", aktor=str(aktor), kalem_id=kalem_id,
        once=once, sonra=sonra, excel_yedek=str(excel_yedek), db_yedek=str(db_yedek),
        bagli_toplamlar=toplamlar,
    )
    return {"basari": True, "kalem": sonra, "ozet": faaliyet_ozeti}
