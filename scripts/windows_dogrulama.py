# -*- coding: utf-8 -*-
"""Windows / OneDrive dogrulamasi: tek ANA + tek YEDEK, kilit, atomik kayit, gecici dosya temizligi, geri donus.

TEST calisma kitaplari uzerinde calisir; uretim ANA/YEDEK dosyalarina ve uretim veritabanina DOKUNMAZ.
Tum dosyalar ``--klasor`` altinda (varsayilan: sistem gecici klasoru) kendi alt klasorunde olusturulur ve sonunda
yalniz bu aracin olusturdugu klasor silinir. OneDrive davranisini denemek icin ``--onedrive-klasoru`` verin
(ornek: %USERPROFILE%\\OneDrive\\Desktop\\Touche); araç orada ``_SMART_EXCEL_TEST_<kimlik>`` alt klasoru acar ve siler.

    py -3.12 scripts\\windows_dogrulama.py
    py -3.12 scripts\\windows_dogrulama.py --onedrive-klasoru "%USERPROFILE%\\OneDrive\\Desktop\\Touche"

Cikis kodu 0 = tum denetimler PASS (Windows disinda 'kilit' denetimleri SKIP). Herhangi bir FAIL -> 1.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK / "scripts"))
sys.path.insert(0, str(KOK / "tests"))

import config  # noqa: E402
import excel_deposu as depo  # noqa: E402
import excel_yazici  # noqa: E402
import guvenlik  # noqa: E402

SONUCLAR: list[tuple[str, str, str]] = []


def kayit(ad: str, durum: str, ayrinti: str = "") -> None:
    SONUCLAR.append((ad, durum, ayrinti))
    print(f"[{durum:4}] {ad}" + (f" - {ayrinti}" if ayrinti else ""), flush=True)


def kontrol(ad: str, kosul: bool, ayrinti: str = "") -> bool:
    kayit(ad, "PASS" if kosul else "FAIL", ayrinti)
    return bool(kosul)


def _kayit(tutar: int = 1000) -> dict:
    return {"Tarih": "2026-10-12", "Faaliyet": "Gürsar İnşaat", "Proje": "Göktürk Okulu", "Cari_Tipi": "Usta",
            "Cari": "Devrim", "Islem_Turu": "Usta/Tedarikçi Ödemesi", "Hesap_Kasa": "Gürsar TL Banka",
            "Tutar": tutar, "PB": "TL", "Kur": 1, "Aciklama": "windows_dogrulama TEST kaydi"}


def _yaz(belge: str, tutar: int):
    return excel_yazici.yaz("gursar_mimarlik", {"_hedef": "ana_ledger", "kayitlar": [_kayit(tutar)]}, belge,
                            aktor="windows_dogrulama")


def _xlsx(klasor: Path) -> list[str]:
    return sorted(str(p.relative_to(klasor)) for p in klasor.rglob("*") if p.suffix.lower() in {".xlsx", ".xlsm", ".xls"})


def _excel_gibi_kilitle(yol: Path):
    """Windows: dosyayi Excel'in yaptigi gibi acik tutar (yalniz OKUMA paylasimi; baskasi yazamaz/degistiremez)."""
    if sys.platform != "win32":
        raise OSError("Yalniz Windows")
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    GENERIC_READ, GENERIC_WRITE, FILE_SHARE_READ, OPEN_EXISTING = 0x80000000, 0x40000000, 0x1, 3
    h = k32.CreateFileW(str(yol), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, None, OPEN_EXISTING, 0x80, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(f"Kilit alinamadi: {ctypes.get_last_error()}")
    return lambda: k32.CloseHandle(h)


def calistir(klasor: Path, etiket: str, yardim: Path) -> None:
    """Bir klasorde tum senaryolari kosar. ``klasor``: bu aracin kendi olusturdugu bos test klasoru (OneDrive
    olabilir); ``yardim``: yerel diskte gecici dosya + denetim kaydi alani (proje temp alanini temsil eder)."""
    from yardimci_excel import tam_akilli_excel

    ana = tam_akilli_excel(klasor / "TEST Touche Holding.xlsx")
    yedek = config.varsayilan_yedek_yolu(ana)
    temp = yardim / f"temp_{etiket}"
    sayfa_once = None
    eski = (config.ana_excel_yolu(), config.yedek_excel_yolu(), config.TEMP_DIR, guvenlik.AUDIT_LOG, guvenlik.LOGLAR)
    audit = yardim / f"audit_{etiket}.jsonl"
    config.ana_excel_ayarla(ana, yedek)
    config.TEMP_DIR = temp
    guvenlik.AUDIT_LOG, guvenlik.LOGLAR = audit, yardim
    on = f"{etiket}: "
    try:
        v1 = depo.sha256(ana)
        sayfa_once = depo.dogrula(ana)["sayfalar"]

        # 1) Excel kapaliyken yazma: V1 -> V2, YEDEK = V1
        s = _yaz("W-1", 1000)
        v2 = depo.sha256(ana)
        kontrol(on + "kapali Excel: yazma basarili", s.get("basari") is True, str(s.get("hata", "")))
        kontrol(on + "yedek = onceki surum (V1)", yedek.exists() and depo.sha256(yedek) == v1 and v2 != v1)
        kontrol(on + "sayfalar korundu", depo.dogrula(ana)["sayfalar"] == sayfa_once)

        # 2) ikinci yazma: V3, YEDEK = V2
        s = _yaz("W-2", 2000)
        v3 = depo.sha256(ana)
        kontrol(on + "ikinci yazma: ANA=V3, YEDEK=V2", s.get("basari") is True and depo.sha256(yedek) == v2 and v3 != v2)

        # 3) tek ANA + tek YEDEK, gecici dosya yok (ANA klasorunde de proje temp alaninda da)
        kontrol(on + "yalniz ANA + YEDEK Excel var", _xlsx(klasor) == sorted([ana.name, yedek.name]), str(_xlsx(klasor)))
        kontrol(on + "gecici dosya kalmadi", depo.gecici_dosyalar() == [] and not [p for p in klasor.iterdir() if ".tmp" in p.name])

        # 4) hata kurtarma: degistirici hata verir -> ANA/YEDEK ayni, gecici yok
        def bozuk(wb):
            raise RuntimeError("simule hata")

        try:
            depo.guvenli_degistir(bozuk, aktor="windows_dogrulama", kaynak="windows_dogrulama")
            basarisiz = False
        except RuntimeError:
            basarisiz = True
        kontrol(on + "hata sonrasi ANA/YEDEK degismedi", basarisiz and depo.sha256(ana) == v3 and depo.sha256(yedek) == v2)
        kontrol(on + "hata sonrasi gecici dosya yok", depo.gecici_dosyalar() == [])

        # 5) Excel acikken (Windows): yazma baslamadan reddedilir, YEDEK ilerlemez
        if os.name == "nt":
            birak = _excel_gibi_kilitle(ana)
            try:
                s = _yaz("W-3", 3000)
                ana_ayni = depo.sha256(ana) == v3
            finally:
                birak()
            kontrol(on + "Excel acik: yazma reddedildi (ANA_KILITLI)", s.get("basari") is False and s.get("hata_kodu") == "ANA_KILITLI",
                    str(s.get("hata_kodu")))
            kontrol(on + "Excel acik: ANA degismedi", ana_ayni)
            kontrol(on + "Excel acik: YEDEK ilerlemedi (son islem geri alinabilir)", depo.sha256(yedek) == v2)
            kontrol(on + "Excel acik: gecici dosya yok", depo.gecici_dosyalar() == [])
            # 5b) Excel kapaninca ayni islem basarili
            s = _yaz("W-3", 3000)
            kontrol(on + "Excel kapaninca yazma basarili", s.get("basari") is True and depo.sha256(yedek) == v3, str(s.get("hata", "")))
            v4 = depo.sha256(ana)
        else:
            kayit(on + "Excel acik (paylasim kilidi)", "SKIP", "yalniz Windows'ta denenir")
            s = _yaz("W-3", 3000)
            v4 = depo.sha256(ana)

        # 6) geri donus = takas: ANA=V4,YEDEK=V3 -> ANA=V3,YEDEK=V4 (ucuncu kalici dosya yok)
        yedekten = depo.yedekten_geri_don(aktor="windows_dogrulama", neden="dogrulama", onay="GERI-DON")
        kontrol(on + "geri donus takas etti (ANA=V3, YEDEK=V4)",
                yedekten.get("basari") is True and depo.sha256(ana) == v3 and depo.sha256(yedek) == v4)
        kontrol(on + "geri donus sonrasi yalniz ANA + YEDEK", _xlsx(klasor) == sorted([ana.name, yedek.name]), str(_xlsx(klasor)))

        # 7) denetim kaydi: islem kimligi, aktor, ANA hash once/sonra
        olaylar = []
        if audit.exists():
            olaylar = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
        islemler = [o for o in olaylar if o.get("olay") == "excel_islem" and o.get("durum") == "BASARILI"]
        kontrol(on + "denetim: basarili islemler kayitli", len(islemler) >= 3, f"{len(islemler)} islem")
        kontrol(on + "denetim: islem_id/aktor/ana_hash_once/ana_hash_sonra",
                all(o.get("islem_id") and o.get("aktor") and o.get("ana_hash_once") and o.get("ana_hash_sonra") for o in islemler))
        kontrol(on + "denetim: geri donus kaydi", any(o.get("olay") == "excel_geri_donus" for o in olaylar))
    finally:
        config.ana_excel_ayarla(eski[0], eski[1])
        config.TEMP_DIR, guvenlik.AUDIT_LOG, guvenlik.LOGLAR = eski[2], eski[3], eski[4]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--klasor", default=None, help="Test klasorlerinin acilacagi ust klasor (varsayilan: sistem gecici klasoru)")
    ap.add_argument("--onedrive-klasoru", default=None, help="OneDrive ile esitlenen bir klasor; orada da denenir")
    ap.add_argument("--bekleme", type=float, default=3.0, help="OneDrive denemesinden sonra gecici dosya kontrolu icin bekleme (sn)")
    a = ap.parse_args(argv)

    uretim = [config.ana_excel_yolu(), config.yedek_excel_yolu()]
    uretim_once = {str(p): depo.sha256(p) for p in uretim if p.exists()}      # test oncesi uretim dosyalarinin izi
    ust = Path(a.klasor) if a.klasor else Path(tempfile.gettempdir())
    ust.mkdir(parents=True, exist_ok=True)
    kimlik = uuid.uuid4().hex[:8]
    acilanlar: list[Path] = []
    try:
        yardim = ust / f"_smart_excel_dogrulama_{kimlik}_yardim"        # yerel disk: temp alani + denetim kaydi
        yerel = ust / f"_smart_excel_dogrulama_{kimlik}"
        for k in (yardim, yerel):
            k.mkdir()
            acilanlar.append(k)
        calistir(yerel, "yerel", yardim)
        if a.onedrive_klasoru:
            od = Path(os.path.expandvars(a.onedrive_klasoru))
            if not od.is_dir():
                kayit("OneDrive klasoru", "FAIL", f"bulunamadi: {od}")
            else:
                alt = od / f"_SMART_EXCEL_TEST_{kimlik}"
                alt.mkdir()
                acilanlar.append(alt)
                calistir(alt, "onedrive", yardim)
                time.sleep(max(0.0, a.bekleme))          # esitleme istemcisi gecici dosya gorebilir mi?
                kontrol("onedrive: bekleme sonrasi klasorde gecici dosya yok", not [p for p in alt.iterdir() if ".tmp" in p.name])
    finally:
        for klasor in acilanlar:                              # yalniz bu aracin olusturdugu klasorler
            shutil.rmtree(klasor, ignore_errors=True)
    uretim_sonra = {str(p): depo.sha256(p) for p in uretim if p.exists()}
    kontrol("uretim ANA/YEDEK dosyalari degismedi", uretim_once == uretim_sonra,
            f"{len(uretim_once)} uretim dosyasi izlendi" if uretim_once else "uretim dosyasi bu makinede yok")
    kontrol("test klasorleri silindi", not any(k.exists() for k in acilanlar))
    fail = [x for x in SONUCLAR if x[1] == "FAIL"]
    atlanan = [x for x in SONUCLAR if x[1] == "SKIP"]
    print(f"\nSONUC: {len(SONUCLAR) - len(fail) - len(atlanan)} PASS, {len(fail)} FAIL, {len(atlanan)} SKIP")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
