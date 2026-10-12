# -*- coding: utf-8 -*-
"""ANA Excel (03_Kayıt Merkezi) ile merkezi SQLite defteri arasinda SALT-OKUNUR mutabakat.

Proje/faaliyet/holding metrikleri merkezi defterden hesaplanir; Excel'de elle girilen
ya da degistirilen satirlar deftere kendiliginden yansimaz. Bu modul farki gorunur kilar
(HICBIR SEY yazmaz, duzeltmez):

  eksik_defterde   Excel'de aktif kayit var, defterde karsiligi yok
  tutar_farki      Ikisinde de var ama TL tutari farkli (Excel sonradan degistirilmis olabilir)
  defterde_fazla   Defterde fis var, Excel'de karsiligi yok (satir Excel'den silinmis olabilir)

Eslestirme anahtari:
  * Kaynak_Belge_ID dolu (uygulamanin yazdigi)  -> fis (belge_id, kayit_sira) = ("<belge>", NNN)
  * Bos (elle girilmis / eski)                  -> belge_id ``LEGACY-<Kayit_ID>``, sira 1
"""
from __future__ import annotations

import re
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import load_workbook

import config
import finans_cekirdegi as cekirdek

_KAYNAK_RE = re.compile(r"^(?P<belge>.+)-(?P<sira>\d{3})$")
# Defterde Excel satiri olmayan, sistemin kendi urettigi fisler (acilis bakiyesi, ters kayit, grup ici cift taraf)
_DEFTER_ONEKLERI = ("OPEN-", "REV-", "IC-", "TRF-", "KUR-", "FX-")


def _kurus(deger) -> int | None:
    try:
        return int((Decimal(str(deger).replace(",", ".")) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        return None


def _excel_satirlari() -> list[dict]:
    ledger = config.FIRMALAR["touche_holding"]["ana_ledger"]
    s = ledger["sutunlar"]
    yol = config.ana_excel_yolu()
    if not Path(yol).exists():
        raise FileNotFoundError(f"Ana Excel bulunamadı: {yol}")
    idx = {ad: ord(h) - ord("A") for ad, h in s.items() if len(h) == 1}     # AA/AB/AC sutunlari gerekmez
    son_sutun = max(idx.values()) + 1
    wb = load_workbook(yol, read_only=True, data_only=True)
    try:
        if ledger["sayfa"] not in wb.sheetnames:
            raise ValueError(f"Sayfa yok: {ledger['sayfa']}")
        ws = wb[ledger["sayfa"]]
        satirlar = []
        for no, v in enumerate(ws.iter_rows(min_row=ledger["ilk_veri_satiri"], max_col=son_sutun, values_only=True),
                               ledger["ilk_veri_satiri"]):
            def h(ad, v=v):
                i = idx.get(ad)
                return v[i] if i is not None and i < len(v) else None
            kayit_id = str(h("Kayit_ID") or "").strip()
            if not kayit_id:
                continue
            satirlar.append({
                "satir": no, "kayit_id": kayit_id, "durum": str(h("Durum") or "").strip(),
                "kaynak_belge": str(h("Kaynak_Belge_ID") or "").strip(),
                "tutar": h("Tutar"), "pb": str(h("PB") or "TL").strip().upper(), "kur": h("Kur"),
                "cari": str(h("Cari") or ""), "proje": str(h("Proje") or ""),
            })
        return satirlar
    finally:
        wb.close()


def _beklenen_anahtar(satir: dict) -> tuple[str, int]:
    m = _KAYNAK_RE.match(satir["kaynak_belge"]) if satir["kaynak_belge"] else None
    if m:
        return m.group("belge"), int(m.group("sira"))
    return f"LEGACY-{satir['kayit_id']}", 1


def _beklenen_tl_kurus(satir: dict) -> int | None:
    tutar = _kurus(satir["tutar"])
    if tutar is None:
        return None
    if satir["pb"] in ("TL", "TRY", ""):
        return tutar
    kur = _kurus(satir["kur"] if satir["kur"] not in (None, "") else 0)
    if not kur:
        return None
    # tutar(kurus) * kur -> TL kurus
    return int((Decimal(tutar) * Decimal(kur) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def kontrol() -> dict:
    """Mutabakat raporu. ANA Excel'i ve SQLite'i yalnizca OKUR."""
    excel = [x for x in _excel_satirlari() if x["durum"].lower() in ("aktif", "")]
    yol = Path(cekirdek.DB_PATH)
    fisler: dict[tuple[str, int], dict] = {}
    if yol.exists():
        con = sqlite3.connect(yol, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        try:
            for r in con.execute(
                "SELECT f.belge_id, f.kayit_sira, f.durum, "
                "(SELECT s.borc_kurus FROM fis_satirlari s WHERE s.fis_id=f.fis_id ORDER BY s.satir_no LIMIT 1) ana_borc "
                "FROM fisler f"
            ):
                fisler[(r["belge_id"], r["kayit_sira"])] = {"durum": r["durum"], "tutar": r["ana_borc"]}
        except sqlite3.OperationalError:
            fisler = {}
        finally:
            con.close()

    eksik, fark, beklenen = [], [], set()
    for x in excel:
        anahtar = _beklenen_anahtar(x)
        beklenen.add(anahtar)
        fis = fisler.get(anahtar)
        ozet = {"satir": x["satir"], "kayit_id": x["kayit_id"], "cari": x["cari"], "proje": x["proje"],
                "excel_tutar": x["tutar"], "pb": x["pb"]}
        if fis is None:
            eksik.append(ozet)
            continue
        beklenen_tutar = _beklenen_tl_kurus(x)
        if beklenen_tutar is not None and fis["tutar"] is not None and fis["durum"] == "MUHASEBELESTI" \
                and int(fis["tutar"]) != beklenen_tutar:
            fark.append({**ozet, "defter_tl_kurus": int(fis["tutar"]), "excel_tl_kurus": beklenen_tutar})
    fazla = sorted(
        {f"{b}-{s:03d}" for (b, s), v in fisler.items()
         if v["durum"] == "MUHASEBELESTI" and (b, s) not in beklenen and not b.startswith(_DEFTER_ONEKLERI)}
    )
    return {"excel_aktif_satir": len(excel), "defter_fis": len(fisler),
            "eksik_defterde": eksik, "tutar_farki": fark, "defterde_fazla": fazla,
            "tutarli": not (eksik or fark or fazla)}


def uyari_metinleri(rapor: dict | None = None) -> list[str]:
    """UI/holding uyarilari icin kisa, anlasilir cumleler."""
    rapor = rapor if rapor is not None else kontrol()
    u = []
    if rapor["eksik_defterde"]:
        u.append(f"Excel'de {len(rapor['eksik_defterde'])} kayıt merkezi defterde yok; bu kayıtlar proje/faaliyet "
                 "toplamlarına yansımıyor (Excel'e elle girilmiş olabilir). Ayrıntı: excel_operasyon.py mutabakat")
    if rapor["tutar_farki"]:
        u.append(f"{len(rapor['tutar_farki'])} kaydın Excel tutarı merkezi defterdekinden farklı "
                 "(Excel sonradan elle değiştirilmiş olabilir).")
    if rapor["defterde_fazla"]:
        u.append(f"Merkezi defterde {len(rapor['defterde_fazla'])} fiş var ama Excel'de aktif karşılığı yok "
                 "(satır silinmiş ya da iptal edilmiş olabilir).")
    return u
