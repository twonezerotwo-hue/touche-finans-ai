# -*- coding: utf-8 -*-
"""Smart Excel operasyon araci (yalniz bilincli insan/operator kullanimi icin).

Komutlar (Windows'ta ``py -3.12 scripts\\excel_operasyon.py <komut>``):

  durum                    ANA ve YEDEK dosyalarinin yolu, boyutu, hash'i, kalinti gecici dosyalar
  dogrula                  ANA ve YEDEK XLSX butunlugunu ve sayfalarini denetler
  envanter [--kok YOL ...] Makinede Touche Smart Excel'e benzeyen dosyalari tarar,
                           ANA ile karsilastirir ve EXCEL_FILE_INVENTORY.md uretir (HICBIR SEY SILMEZ)
  temizle  [--kok YOL ...] Envanterde "artik uretilmis kopya" ve "ANA'da bulunmayan kayit yok"
                           olarak kanitlanan dosyalari listeler. Silmek icin --sil ve
                           onay kelimesi (--onay SIL) gerekir.
  yedek-olustur            YEDEK yoksa ANA'nin dogrulanmis kopyasini olusturur (var ve farkliysa --onay YENILE ister:
                           eski YEDEK = onceki surum kaybolur). Normal calismada YEDEK her yazmada otomatik yenilenir.
  kanit DOSYA              Bir dosyanin ANA tarafindan KAPSANDIGINA dair kanit raporu (okunur mu, ledger sayfasi, bilinmeyen
                           sayfa, ANA'da olmayan Kayit_ID / Kalem_ID). Hicbir sey degistirmez.
  mutabakat                ANA Excel ile merkezi defteri karsilastirir (SALT-OKUNUR): Excel'de elle girilmis /
                           degistirilmis satirlar deftere yansimiyor mu? Hicbir sey degistirmez.
  defteri-aktar            Deftere yansimamis Excel satirlarini listeler; --onay AKTAR ile merkezi SQLite
                           deftere aktarir (Excel'e DOKUNMAZ, idempotent).
  geri-don                 ANA ile YEDEK'i takas eder (acil durum). --onay GERI-DON ister.
  entegre-et DOSYA         Onaylanmis, disarida duzenlenmis bir XLSX'i yeni ANA yapar (YEDEK=onceki ANA).

Bu arac yapay zeka katmanindan veya HTTP sunucusundan ASLA cagrilmaz.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import excel_deposu as depo  # noqa: E402

# Bu sistemin gecmiste urettigi ve artik gereksiz olan kopyalarin ad/klasor kaliplari.
URETILMIS_KOPYA_DESENLERI = (
    "firmalar/*/yedek/*.xlsx",            # eski tarihli yedekler
    "firmalar/kurulum_yedek/**/*.xlsx",   # eski kurulum yedekleri
    "arsiv/excel_yedek/*.xlsx",           # eski entegrasyon yedekleri
    "*_vade_[0-9]*.xlsx",                 # eski vade guncelleme yedekleri
    "Gursar_ONCE_HoldingOzeti_*.xlsx",    # eski ozet betigi yedekleri
    "*_hesap.xlsx",                       # eski faaliyet-basina kopyalar
    ".*.tmp.xlsx",                        # artik gecici dosyalar
    "*_once_*.xlsx",
)
ILGILI_AD_ANAHTARLARI = ("touche", "gursar", "gürsar", "smart excel", "ai ready", "finans yönetim", "finans yonetim")


def _hash(yol: Path) -> str:
    return depo.sha256(yol)


def _sayfalar(yol: Path):
    try:
        with zipfile.ZipFile(yol) as z:
            if "xl/workbook.xml" not in z.namelist():
                return None
        from openpyxl import load_workbook

        wb = load_workbook(yol, read_only=True, data_only=False)
        try:
            return list(wb.sheetnames)
        finally:
            wb.close()
    except Exception:
        return None


def _kayit_kimlikleri(yol: Path) -> set[str]:
    """03_Kayit Merkezi'ndeki Kayit_ID degerlerini dondurur (yoksa bos)."""
    from openpyxl import load_workbook

    ledger = config.FIRMALAR["touche_holding"]["ana_ledger"]
    try:
        wb = load_workbook(yol, read_only=True, data_only=True)
    except Exception:
        return set()
    try:
        if ledger["sayfa"] not in wb.sheetnames:
            return set()
        ws = wb[ledger["sayfa"]]
        sutun = ledger["sutunlar"]["Kayit_ID"]
        idx = ord(sutun) - ord("A")
        kimlikler = set()
        for satir in ws.iter_rows(min_row=ledger["ilk_veri_satiri"], values_only=True):
            if len(satir) > idx and satir[idx]:
                kimlikler.add(str(satir[idx]).strip())
        return kimlikler
    finally:
        wb.close()


KALEM_SAYFASI = "06_Proje İş Kalemleri"


def _kolon_degerleri(yol: Path, sayfa: str, sutun_idx: int, ilk: int) -> set[str]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(yol, read_only=True, data_only=True)
    except Exception:
        return set()
    try:
        if sayfa not in wb.sheetnames:
            return set()
        sonuc = set()
        for satir in wb[sayfa].iter_rows(min_row=ilk, values_only=True):
            if len(satir) > sutun_idx and satir[sutun_idx] not in (None, ""):
                sonuc.add(str(satir[sutun_idx]).strip())
        return sonuc
    finally:
        wb.close()


def _dolu_sayfalar(yol: Path) -> dict[str, bool] | None:
    """{sayfa: icinde veri var mi}; dosya okunamazsa None."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(yol, read_only=True, data_only=False)
    except Exception:
        return None
    try:
        sonuc = {}
        for ws in wb.worksheets:
            dolu = False
            for satir in ws.iter_rows(max_row=400, values_only=True):
                if any(v not in (None, "") for v in satir):
                    dolu = True
                    break
            sonuc[ws.title] = dolu
        return sonuc
    finally:
        wb.close()


def kapsama_sorunlari(yol: Path, ana_sayfalar: set[str], ana_kalem_idleri: set[str]) -> dict:
    """Aday dosyanin ANA tarafindan KAPSANDIGINI kanitlamak icin gereken kontroller.

    * ledger sayfasi (03_Kayit Merkezi) olmali; aksi halde Kayit_ID karsilastirmasi anlamsiz kalir
    * ANA'da bulunmayan, icinde veri olan sayfa olmamali
    * ANA'da olmayan is kalemi kimligi (06_Proje Is Kalemleri, A sutunu) olmamali
    Her sorun silmeyi ENGELLER (kanit yok -> silinmez).
    """
    sorunlar = []
    ledger = config.FIRMALAR["touche_holding"]["ana_ledger"]["sayfa"]
    dolu = _dolu_sayfalar(yol)
    if dolu is None:
        return {"sorunlar": ["dosya okunamadi"], "tekil_kalem": []}
    if ledger not in dolu:
        sorunlar.append(f"'{ledger}' sayfasi yok (kimlik karsilastirmasi yapilamaz)")
    sorunlar += [f"ANA'da olmayan dolu sayfa: {ad}" for ad, v in dolu.items() if v and ad not in ana_sayfalar]
    kalemler = _kolon_degerleri(yol, KALEM_SAYFASI, 0, 6)
    tekil_kalem = sorted(kalemler - ana_kalem_idleri)
    if tekil_kalem:
        sorunlar.append(f"ANA'da olmayan is kalemi kimligi: {', '.join(tekil_kalem[:5])}")
    return {"sorunlar": sorunlar, "tekil_kalem": tekil_kalem}


def _ilgili_mi(yol: Path) -> bool:
    """Dosya Touche Smart Excel ile ilgili mi? Yalniz DOSYA ADINA veya bu sistemin kendi
    urettigi klasorlere (firmalar/, arsiv/) bakilir; ust klasor adi yeterli sayilmaz.
    """
    ad = yol.name.lower()
    if any(k in ad for k in ILGILI_AD_ANAHTARLARI):
        return True
    for uretilen in (config.BASE_DIR / "firmalar", config.BASE_DIR / "arsiv"):
        try:
            yol.resolve().relative_to(uretilen.resolve())
            return True
        except ValueError:
            continue
    return False


def _uretilmis_kopya_mi(yol: Path, kok: Path) -> bool:
    try:
        goreli = yol.relative_to(kok).as_posix()
    except ValueError:
        goreli = yol.name
    return any(fnmatch.fnmatch(goreli, d) or fnmatch.fnmatch(yol.name, d) for d in URETILMIS_KOPYA_DESENLERI)


def varsayilan_kokler() -> list[Path]:
    kokler = [config.BASE_DIR, config.ana_excel_yolu().parent, config.yedek_excel_yolu().parent]
    if config.LEGACY_GURSAR_EXCEL.parent.exists():
        kokler.append(config.LEGACY_GURSAR_EXCEL.parent)
    return list(dict.fromkeys(k for k in kokler if k.exists()))


def tara(kokler) -> list[dict]:
    ana, yedek = config.ana_excel_yolu().resolve(), config.yedek_excel_yolu().resolve()
    ana_idleri = _kayit_kimlikleri(ana) if ana.exists() else set()
    ana_hash = _hash(ana) if ana.exists() else None
    ana_sayfalar = set(_sayfalar(ana) or []) if ana.exists() else set()
    ana_kalem_idleri = _kolon_degerleri(ana, KALEM_SAYFASI, 0, 6) if ana.exists() else set()
    bulunanlar, gorulen = [], set()
    for kok in kokler:
        for dizin, alt, dosyalar in os.walk(kok):
            alt[:] = [a for a in alt if a not in {".git", "node_modules", "__pycache__", ".venv", "venv"}]
            for ad in dosyalar:
                yol = Path(dizin) / ad
                if yol.suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}:
                    continue
                gercek = yol.resolve()
                if gercek in gorulen:
                    continue
                gorulen.add(gercek)
                if not _ilgili_mi(yol) and gercek not in (ana, yedek):
                    continue
                bilgi = {"yol": gercek, "boyut": yol.stat().st_size,
                         "degisim": datetime.fromtimestamp(yol.stat().st_mtime).isoformat(timespec="seconds"),
                         "sha256": _hash(yol),
                         "sayfalar": _sayfalar(yol) if yol.suffix.lower() in {".xlsx", ".xlsm"} else None}
                if yol.suffix.lower() == ".csv":
                    bilgi.update(rol="CSV(silinmez)", tekil_kayit=[], uretilmis_kopya=False)   # yalniz listelenir
                elif gercek == ana:
                    bilgi["rol"] = "MAIN"
                elif gercek == yedek:
                    bilgi["rol"] = "BACKUP"
                else:
                    ids = _kayit_kimlikleri(yol) if yol.suffix.lower() == ".xlsx" else set()
                    bilgi["tekil_kayit"] = sorted(ids - ana_idleri)
                    bilgi["ana_ile_ayni"] = bilgi["sha256"] == ana_hash
                    bilgi["uretilmis_kopya"] = _uretilmis_kopya_mi(yol, config.BASE_DIR)
                    bilgi["kapsama_sorunlari"] = []
                    if yol.suffix.lower() == ".xlsx" and not bilgi["ana_ile_ayni"] and bilgi["sayfalar"] is not None:
                        k = kapsama_sorunlari(yol, ana_sayfalar, ana_kalem_idleri)
                        bilgi["kapsama_sorunlari"] = k["sorunlar"]
                        bilgi["tekil_kalem"] = k["tekil_kalem"]
                    if bilgi["ana_ile_ayni"]:
                        bilgi["rol"] = "KOPYA(ANA ile birebir ayni)"
                    elif bilgi["tekil_kayit"]:
                        bilgi["rol"] = "TEKIL_KAYIT_VAR(silinmez)"
                    elif yol.suffix.lower() == ".xls" or bilgi["sayfalar"] is None:
                        bilgi["rol"] = "OKUNAMADI/ESKI_FORMAT(silinmez)"
                    elif bilgi["kapsama_sorunlari"]:
                        bilgi["rol"] = "KAPSAMA_KANITLANAMADI(silinmez)"
                    elif bilgi["uretilmis_kopya"]:
                        bilgi["rol"] = "ESKI_URETILMIS_KOPYA"
                    else:
                        bilgi["rol"] = "BELIRSIZ(silinmez)"
                bulunanlar.append(bilgi)
    return sorted(bulunanlar, key=lambda b: (b.get("rol") != "MAIN", b.get("rol") != "BACKUP", str(b["yol"])))


def kanit_temiz(b: dict) -> bool:
    """ANA'nin dosyayi kapsadigi kanitlandi mi: okunabilir, tekil kayit/kalem yok, kapsama sorunu yok."""
    return bool(b.get("sayfalar") is not None and not b.get("tekil_kayit") and not b.get("tekil_kalem")
                and not b.get("kapsama_sorunlari"))


def silinebilir(b: dict, dahil=()) -> bool:
    """Yalniz kanitlanmis, bu sistemin urettigi artik kopyalar silinebilir.

    Kanit: (a) dosya adi/klasoru sistemin urettigi kopya kaliplarindan biri, VE
    (b) ANA ile birebir ayni ya da icindeki hicbir kayit/kalem kimligi ANA'da eksik degil, ledger sayfasi okunabilir
    ve ANA'da olmayan dolu sayfa yok. Okunamayan veya kapsami kanitlanamayan dosya ASLA silinebilir degildir.
    ``dahil``: operatorun acikca ekledigi siniflar (yalniz ``TEST``); bunlarda kalip yerine ad kanitı kullanilir ama
    kapsama kaniti (b) YINE zorunludur."""
    if b.get("rol") in {"ESKI_URETILMIS_KOPYA", "KOPYA(ANA ile birebir ayni)"} and b.get("uretilmis_kopya") \
            and not b.get("tekil_kayit") and not b.get("kapsama_sorunlari") and not b.get("tekil_kalem"):
        return True
    return bool("TEST" in dahil and siniflandir(b) == "TEST" and kanit_temiz(b))


def siniflandir(b: dict) -> str:
    """Operasyon raporu icin sade sinif: CANONICAL_MAIN, CANONICAL_BACKUP, LEGACY_BACKUP, OLD_MAIN, TEMP, TEST,
    ARCHIVE, UNKNOWN, UNIQUE_DATA_REVIEW_REQUIRED, SAFE_TO_DELETE. SAFE_TO_DELETE/TEMP/LEGACY_BACKUP + 'silinebilir' = silinebilir."""
    rol = b.get("rol", "")
    if rol == "MAIN":
        return "CANONICAL_MAIN"
    if rol == "BACKUP":
        return "CANONICAL_BACKUP"
    yol = Path(b["yol"])
    ad = yol.name.lower()
    if b.get("tekil_kayit") or b.get("tekil_kalem"):
        return "UNIQUE_DATA_REVIEW_REQUIRED"
    if ad.startswith(".") and ".tmp" in ad:
        return "TEMP"
    if silinebilir(b):
        return "SAFE_TO_DELETE"
    try:
        if yol.resolve() == config.LEGACY_GURSAR_EXCEL.resolve():
            return "OLD_MAIN"
    except OSError:
        pass
    parcalar = {p.lower() for p in yol.parts}
    if rol.startswith("CSV") and ({"arsiv", "muhasebe_export"} & parcalar):
        return "ARCHIVE"
    if "test" in ad:
        return "TEST"
    if b.get("uretilmis_kopya"):
        return "LEGACY_BACKUP"            # kalibi uyuyor ama kapsami kanitlanamadi / okunamadi -> insan karari
    return "UNKNOWN"


URETILMIS_KLASOR_ADLARI = {"yedek", "kurulum_yedek", "excel_yedek"}


def bos_uretilmis_klasorleri_sil(klasorler) -> list[Path]:
    """Silme sonrasi BOS kalan, bu sistemin urettigi yedek klasorlerini (firmalar/**/yedek, firmalar/kurulum_yedek/**,
    arsiv/excel_yedek) kaldirir. ``rmdir`` yalniz bos klasoru siler; baska ad/yer ve dolu klasor asla etkilenmez."""
    kokler = [(config.BASE_DIR / "firmalar").resolve(), (config.BASE_DIR / "arsiv").resolve()]
    silinen = []
    for k in sorted({Path(x).resolve() for x in klasorler}, key=lambda p: -len(p.parts)):
        adaylar = [k, *k.parents]
        for d in adaylar:
            if not any(d != kok and kok in d.parents for kok in kokler):
                break
            kurulum_alti = "kurulum_yedek" in d.parts
            if d.name not in URETILMIS_KLASOR_ADLARI and not (kurulum_alti and d.parent.name == "kurulum_yedek"):
                break
            try:
                d.rmdir()
            except OSError:
                break                       # bos degil
            silinen.append(d)
    return silinen


def rapor_yaz(bulunanlar, hedef: Path):
    s = ["# EXCEL_FILE_INVENTORY", "",
         f"Üretim zamanı: {datetime.now().isoformat(timespec='seconds')}  ",
         f"MAIN: `{config.ana_excel_yolu()}`  ", f"BACKUP: `{config.yedek_excel_yolu()}`", "",
         "| # | Sınıf | Rol | Yol | Boyut | Değişim | SHA-256 | Sayfa | ANA'da olmayan kayıt/kalem | Silinebilir | Neden silinmez |",
         "|---|-------|-----|-----|------:|---------|---------|------:|---------------------:|:-----------:|----------------|"]
    for i, b in enumerate(bulunanlar, 1):
        neden = "; ".join(b.get("kapsama_sorunlari") or []) or "-"
        s.append(f"| {i} | {siniflandir(b)} | {b.get('rol')} | `{b['yol']}` | {b['boyut']:,} | {b['degisim']} | "
                 f"`{b['sha256'][:16]}…` | {len(b['sayfalar']) if b['sayfalar'] else '-'} | "
                 f"{len(b.get('tekil_kayit', [])) + len(b.get('tekil_kalem', []))} | {'EVET' if silinebilir(b) else 'hayır'} | {neden} |")
    sil = [b for b in bulunanlar if silinebilir(b)]
    s += ["", f"Toplam {len(bulunanlar)} ilgili dosya; silinebilir (kanıtlı) {len(sil)}.", "",
          "Kural: yalnız bu sistemin ürettiği (yedek/kurulum/tarihli/geçici) VE ANA'da bulunmayan kaydı "
          "OLMAYAN dosyalar silinebilir. Tekil kayıt içeren veya belirsiz dosyalar asla otomatik silinmez."]
    hedef.write_text("\n".join(s) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    alt = ap.add_subparsers(dest="komut", required=True)
    alt.add_parser("durum")
    alt.add_parser("dogrula")
    alt.add_parser("mutabakat")
    p = alt.add_parser("defteri-aktar")
    p.add_argument("--onay", default="")
    for ad in ("envanter", "temizle"):
        p = alt.add_parser(ad)
        p.add_argument("--kok", action="append", help="Taranacak klasör (birden fazla verilebilir)")
        p.add_argument("--rapor", default=None,
                       help="Rapor dosyasi (varsayilan: <base>/loglar/excel_envanter_<tarih>.md; git'e girmez)")
        if ad == "temizle":
            p.add_argument("--sil", action="store_true")
            p.add_argument("--onay", default="")
            p.add_argument("--dahil", default="", help="Ek silinebilir sinif (yalniz TEST; kapsama kaniti yine zorunlu)")
    p = alt.add_parser("yedek-olustur")
    p.add_argument("--onay", default="")
    p.add_argument("--aktor", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator")
    p = alt.add_parser("kanit")
    p.add_argument("dosya")
    p = alt.add_parser("geri-don")
    p.add_argument("--neden", required=True)
    p.add_argument("--aktor", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator")
    p.add_argument("--onay", default="")
    p = alt.add_parser("entegre-et")
    p.add_argument("dosya")
    p.add_argument("--neden", default="")
    p.add_argument("--aktor", default=os.environ.get("USERNAME") or os.environ.get("USER") or "operator")
    a = ap.parse_args(argv)

    if a.komut == "durum":
        d = depo.durum()
        for rol in ("ana", "yedek"):
            x = d[rol]
            print(f"{rol.upper():6} {x['yol']}\n       var={x['var']} boyut={x['boyut']:,} degisim={x['degisim']}\n       sha256={x['sha256']}")
        print(f"ANA = YEDEK mi: {d['ana_yedek_ayni']}")
        print(f"Kalıntı geçici dosya: {len(d['gecici_dosyalar'])}")
        for g in d["gecici_dosyalar"]:
            print("  ", g)
        return 0
    if a.komut == "dogrula":
        kod = 0
        for ad, yol in (("ANA", config.ana_excel_yolu()), ("YEDEK", config.yedek_excel_yolu())):
            try:
                print(f"{ad}: OK sayfalar={depo.dogrula(yol)['sayfalar']}")
            except Exception as exc:
                print(f"{ad}: HATA {exc}")
                kod = 1
        return kod
    if a.komut == "mutabakat":
        import excel_defter_mutabakati as mut

        r = mut.kontrol()
        print(f"Excel aktif satır: {r['excel_aktif_satir']} · Defter fişi: {r['defter_fis']}")
        for ad, baslik in (("eksik_defterde", "Excel'de var, defterde YOK"), ("tutar_farki", "Tutar farkı")):
            print(f"\n{baslik}: {len(r[ad])}")
            for x in r[ad][:50]:
                print("  -", {k: v for k, v in x.items() if v not in (None, "")})
        print(f"\nDefterde fazla (Excel'de aktif karşılığı yok): {len(r['defterde_fazla'])}")
        for x in r["defterde_fazla"][:50]:
            print("  -", x)
        print("\nSonuç:", "TUTARLI" if r["tutarli"] else "FARK VAR (hiçbir şey değiştirilmedi)")
        return 0 if r["tutarli"] else 3
    if a.komut == "defteri-aktar":
        import excel_defter_mutabakati as mut
        import finans_cekirdegi as cekirdek

        eksik = mut.kontrol()["eksik_defterde"]
        print(f"Merkezi defterde karşılığı olmayan Excel kaydı: {len(eksik)}")
        for x in eksik[:50]:
            print("  -", x["kayit_id"], x["cari"], x["proje"], x["excel_tutar"], x["pb"])
        if not eksik:
            return 0
        if a.onay != "AKTAR":
            print("Bu kayıtları merkezi deftere aktarmak için: defteri-aktar --onay AKTAR (Excel'e dokunulmaz).")
            return 0
        sonuc = cekirdek.holding_excelini_aktar()
        print("Aktarım:", sonuc)
        return 0 if not sonuc["hatalar"] else 4
    if a.komut == "yedek-olustur":
        from guvenlik import audit_yaz
        from kayit_kilidi import yazma_kilidi

        ana, yedek = config.ana_excel_yolu(), config.yedek_excel_yolu()
        with yazma_kilidi():
            depo.gecici_kalintilari_temizle()
            depo.dogrula(ana)
            if yedek.exists():
                if depo.sha256(yedek) == depo.sha256(ana):
                    print("YEDEK zaten ANA ile birebir ayni; yapilacak bir sey yok.")
                    return 0
                if a.onay != "YENILE":
                    print("YEDEK zaten var ve ANA'dan FARKLI (onceki surum). Uzerine yazmak o surumu kaybettirir: "
                          "gerekiyorsa --onay YENILE ile tekrar calistirin. Hicbir sey degismedi.")
                    return 2
            once = depo.sha256(yedek) if yedek.exists() else None
            h = depo.yedegi_yenile()
            audit_yaz("excel_yedek_olusturuldu", aktor=a.aktor, ana=str(ana), yedek=str(yedek), yedek_hash_once=once,
                      ana_hash=h, yedek_hash=depo.sha256(yedek))
        print(f"YEDEK olusturuldu/yenilendi: {yedek}\n  sha256={h}")
        return 0
    if a.komut == "kanit":
        yol = Path(a.dosya)
        ana = config.ana_excel_yolu()
        if not yol.exists():
            print("Dosya yok.")
            return 1
        sayfalar = _sayfalar(yol) if yol.suffix.lower() in {".xlsx", ".xlsm"} else None
        k = kapsama_sorunlari(yol, set(_sayfalar(ana) or []), _kolon_degerleri(ana, KALEM_SAYFASI, 0, 6)) if sayfalar else \
            {"sorunlar": ["dosya okunamadi / Excel degil"], "tekil_kalem": []}
        tekil = sorted(_kayit_kimlikleri(yol) - _kayit_kimlikleri(ana)) if sayfalar else []
        ayni = depo.sha256(yol) == depo.sha256(ana)
        print(f"Dosya: {yol}\n  ANA ile birebir ayni: {ayni}\n  sayfalar: {sayfalar}\n"
              f"  ANA'da olmayan Kayit_ID: {len(tekil)} {tekil[:10]}\n  ANA'da olmayan Kalem_ID: {k['tekil_kalem']}\n"
              f"  kapsama sorunlari: {k['sorunlar'] or 'YOK'}")
        kapsanir = ayni or (sayfalar is not None and not tekil and not k["sorunlar"])
        print("SONUC:", "ANA bu dosyayi KAPSIYOR (silinmesi bilgi kaybi dogurmaz)" if kapsanir
              else "KAPSAMA KANITLANAMADI - silmeyin, gozden gecirin")
        return 0 if kapsanir else 3
    if a.komut in ("envanter", "temizle"):
        kokler = [Path(k) for k in a.kok] if a.kok else varsayilan_kokler()
        bulunanlar = tara(kokler)
        rapor = Path(a.rapor) if a.rapor else Path(config.LOGLAR) / f"excel_envanter_{datetime.now():%Y%m%d_%H%M%S}.md"
        rapor.parent.mkdir(parents=True, exist_ok=True)
        rapor_yaz(bulunanlar, rapor)
        print(f"{len(bulunanlar)} ilgili dosya bulundu; rapor: {rapor}")
        for b in bulunanlar:
            print(f"  [{siniflandir(b)}] [{b.get('rol')}] {b['yol']}")
        if a.komut == "envanter":
            return 0
        dahil = {x.strip().upper() for x in a.dahil.split(",") if x.strip()}
        if dahil - {"TEST"}:
            print(f"--dahil yalniz TEST olabilir (verilen: {sorted(dahil - {'TEST'})}); hicbir sey silinmedi.")
            return 2
        silinecek = [b for b in bulunanlar if silinebilir(b, dahil)]
        print(f"\nSilinebilir (kanitli artik kopya): {len(silinecek)}")
        for b in silinecek:
            print("  -", b["yol"])
        if not a.sil:
            print("Silmek icin: temizle --sil --onay SIL")
            return 0
        if a.onay != "SIL":
            print("Onay kelimesi 'SIL' eksik; hicbir sey silinmedi.")
            return 2
        from guvenlik import audit_yaz

        silinen_klasorler = set()
        for b in silinecek:
            audit_yaz("excel_dosyasi_silindi", yol=str(b["yol"]), sha256=b["sha256"], boyut=b["boyut"],
                      sinif=siniflandir(b), rol=b.get("rol"), kanit="kalip+kapsama" if not dahil else "kalip/ad+kapsama")
            b["yol"].unlink()
            silinen_klasorler.add(b["yol"].parent)
            print("silindi:", b["yol"])
        for klasor in bos_uretilmis_klasorleri_sil(silinen_klasorler):
            print("bos uretilmis yedek klasoru silindi:", klasor)
        return 0
    if a.komut == "geri-don":
        sonuc = depo.yedekten_geri_don(aktor=a.aktor, neden=a.neden, onay=a.onay)
        print("Geri dönüş tamamlandı:", sonuc)
        return 0
    if a.komut == "entegre-et":
        sonuc = depo.harici_dosyayi_ana_yap(a.dosya, aktor=a.aktor, neden=a.neden)
        print("ANA güncellendi:", sonuc)
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except depo.DepoHatasi as exc:
        print(f"HATA [{exc.kod}]: {exc.mesaj}", file=sys.stderr)
        sys.exit(1)
