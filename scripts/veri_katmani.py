# -*- coding: utf-8 -*-
"""Ortak veri katmani: baglanti, ek (additive) sarma ve holding dimension semasi.

Mevcut merkezi defter (finans_cekirdegi) degistirilmez. Bu modul yalnizca
yeni tablolar ve yeni (nullable) sutunlar ekler. Var olan sutunlar yeniden
adlandirilmaz veya silinmez; boylece production verisi korunur.

Hiyerarsi hedefi:
    Holding -> Faaliyet Alani (business unit) -> Sirket -> Proje
    -> Sozlesme -> Hakedis -> Is Kalemi -> Finansal Islem -> Belge
"""

from __future__ import annotations

from contextlib import contextmanager

import finans_cekirdegi as cekirdek

# Finansal hareketlere eklenecek dimension kolonlari (nullable, additive).
OLCU_KOLONLARI = {
    "fisler": {
        "aktivite_id": "TEXT",       # faaliyet alani / business unit
        "sozlesme_id": "TEXT",
        "hakedis_id": "TEXT",
        "is_kalemi_id": "TEXT",
        "masraf_merkezi_id": "TEXT",  # cost center
        "kategori_id": "TEXT",
        "karsi_taraf_id": "TEXT",     # counterparty
    },
    "faaliyetler": {
        "alan_id": "TEXT",            # sirket(faaliyet) -> faaliyet alani bagi
    },
}

EK_TABLOLAR = """
CREATE TABLE IF NOT EXISTS faaliyet_alanlari (
  alan_id TEXT PRIMARY KEY, ad TEXT NOT NULL, holding_key TEXT NOT NULL DEFAULT 'touche_holding',
  aktif INTEGER NOT NULL DEFAULT 1, guncelleme TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sozlesmeler (
  sozlesme_id TEXT PRIMARY KEY, faaliyet_key TEXT NOT NULL, alan_id TEXT, proje TEXT NOT NULL DEFAULT '',
  musteri TEXT, tutar_kurus INTEGER NOT NULL DEFAULT 0, para_birimi TEXT NOT NULL DEFAULT 'TL',
  baslangic TEXT, bitis TEXT, durum TEXT NOT NULL DEFAULT 'AKTIF', aciklama TEXT,
  olusturan TEXT, olusturma TEXT NOT NULL,
  FOREIGN KEY(faaliyet_key) REFERENCES faaliyetler(faaliyet_key)
);
CREATE TABLE IF NOT EXISTS hakedisler (
  hakedis_id TEXT PRIMARY KEY, sozlesme_id TEXT, faaliyet_key TEXT NOT NULL, proje TEXT NOT NULL DEFAULT '',
  tarih TEXT NOT NULL, vade TEXT, fatura_no TEXT, brut_kurus INTEGER NOT NULL DEFAULT 0,
  kesinti_kurus INTEGER NOT NULL DEFAULT 0, net_kurus INTEGER NOT NULL DEFAULT 0,
  durum TEXT NOT NULL DEFAULT 'TASLAK', olusturan TEXT, olusturma TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS is_kalemleri (
  kalem_id TEXT PRIMARY KEY, sozlesme_id TEXT, faaliyet_key TEXT NOT NULL, proje TEXT NOT NULL DEFAULT '',
  maliyet_grubu TEXT, is_kalemi TEXT NOT NULL, birim TEXT, metraj_mikro INTEGER NOT NULL DEFAULT 0,
  satis_birim_fiyati_kurus INTEGER NOT NULL DEFAULT 0, maliyet_birim_fiyati_kurus INTEGER NOT NULL DEFAULT 0,
  butce_kurus INTEGER NOT NULL DEFAULT 0, taahhut_kurus INTEGER NOT NULL DEFAULT 0,
  gerceklesen_kurus INTEGER NOT NULL DEFAULT 0, tahmin_kurus INTEGER NOT NULL DEFAULT 0,
  durum TEXT NOT NULL DEFAULT 'AKTIF', guncelleme TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kategoriler (
  kategori_id TEXT PRIMARY KEY, ad TEXT NOT NULL, tip TEXT NOT NULL DEFAULT 'GIDER',
  masraf_merkezi INTEGER NOT NULL DEFAULT 0, aktif INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS karsi_taraflar (
  taraf_id TEXT PRIMARY KEY, ad TEXT NOT NULL, tip TEXT NOT NULL DEFAULT 'MUSTERI',
  vergi_no TEXT, faaliyet_key TEXT, aktif INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, aktor TEXT, eylem TEXT NOT NULL,
  varlik_tipi TEXT, varlik_id TEXT, onceki TEXT, sonraki TEXT, zaman TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS senaryolar (
  senaryo_id TEXT PRIMARY KEY, ad TEXT NOT NULL, tip TEXT NOT NULL DEFAULT 'BASE',
  varsayimlar TEXT NOT NULL DEFAULT '{}', sonuc TEXT NOT NULL DEFAULT '{}',
  olusturan TEXT, olusturma TEXT NOT NULL, guncelleme TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS belge_kuyrugu (
  belge_id TEXT PRIMARY KEY, durum TEXT NOT NULL DEFAULT 'UPLOAD', tur TEXT,
  guven INTEGER, eslesme TEXT, alanlar TEXT NOT NULL DEFAULT '{}',
  adimlar TEXT NOT NULL DEFAULT '[]', olusturan TEXT,
  olusturma TEXT NOT NULL, guncelleme TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_is_kalem_faaliyet ON is_kalemleri(faaliyet_key);
CREATE INDEX IF NOT EXISTS idx_audit_varlik ON audit_log(varlik_tipi, varlik_id);
CREATE INDEX IF NOT EXISTS idx_belge_durum ON belge_kuyrugu(durum);
CREATE TABLE IF NOT EXISTS tahmin_dogrulugu (
  id INTEGER PRIMARY KEY AUTOINCREMENT, donem TEXT NOT NULL, olcu TEXT NOT NULL DEFAULT 'NAKIT',
  onceki_tahmin_kurus INTEGER NOT NULL DEFAULT 0, gerceklesen_kurus INTEGER NOT NULL DEFAULT 0,
  hata_kurus INTEGER NOT NULL DEFAULT 0, hata_yuzde REAL NOT NULL DEFAULT 0,
  guncelleme TEXT NOT NULL
);
"""

# Yeni dimension kolonlarina bagli indeksler; ALTER TABLE sonrasinda kurulur.
EK_INDEKSLER = """
CREATE INDEX IF NOT EXISTS idx_fis_sozlesme ON fisler(sozlesme_id);
CREATE INDEX IF NOT EXISTS idx_fis_kalem ON fisler(is_kalemi_id);
CREATE INDEX IF NOT EXISTS idx_fis_kategori ON fisler(kategori_id);
"""


@contextmanager
def baglanti():
    """Merkezi defter baglantisi (test edilebilirlik icin DB_PATH yamalanabilir)."""
    with cekirdek._baglanti() as con:  # bilincli paylasim
        yield con


def _kolon_var(con, tablo: str, kolon: str) -> bool:
    return any(r[1] == kolon for r in con.execute(f"PRAGMA table_info({tablo})").fetchall())


def sema_guncelle() -> None:
    """Ek semayi idempotent bicimde olusturur; var olan veriyi degistirmez."""
    with baglanti() as con:
        con.executescript(EK_TABLOLAR)
        for tablo, kolonlar in OLCU_KOLONLARI.items():
            for kolon, tip in kolonlar.items():
                if not _kolon_var(con, tablo, kolon):
                    con.execute(f"ALTER TABLE {tablo} ADD COLUMN {kolon} {tip}")
        con.executescript(EK_INDEKSLER)


_HAZIRLANAN: set[str] = set()


def veritabani_hazirla() -> None:
    """Merkezi defteri ve holding dimension semasini birlikte hazirlar.

    Ayni veritabani yolu icin sema bir kez kurulur; tekrarlayan cagrilar
    hizli doner. Test edilebilirlik icin DB_PATH yolu degisirse yeniden kurulur.
    """
    yol = str(cekirdek.DB_PATH)
    if yol in _HAZIRLANAN and cekirdek.DB_PATH.exists():
        return
    cekirdek.veritabanini_hazirla()
    sema_guncelle()
    _HAZIRLANAN.add(yol)



def demo_holding_hierarchy_yukle(*, zorla: bool = False) -> dict:
    """Ornek hiyerarsiyi (Touche -> Gursar -> Gokturk -> Tas isleri -> kalemler)
    acik cagriyla yukler. Otomatik calismaz; demo verisi gercek veri gibi
    sunulmaz. Is kalemleri varsa ve `zorla` degilse hicbir sey yazmaz.
    """
    from datetime import datetime

    simdi = datetime.now().isoformat(timespec="seconds")
    kalemler = [
        ("KLM-TAS-BASAMAK", "Tas Isleri", "Basamak", "m2", 120_000, 850, 610, 1_200_000),
        ("KLM-TAS-BORDUR", "Tas Isleri", "Bordur", "m", 900_000, 240, 170, 980_000),
        ("KLM-TAS-TERRAZZO", "Tas Isleri", "Terrazzo", "m2", 45_000, 1_450, 1_230, 720_000),
        ("KLM-SAHA-BETON", "Saha Betonu", "Saha Betonu", "m3", 12_000, 1_900, 1_480, 880_000),
    ]
    with baglanti() as con:
        if not zorla and con.execute("SELECT COUNT(*) FROM is_kalemleri").fetchone()[0]:
            return {"yuklendi": 0, "sebep": "Is kalemleri mevcut; demo yuklenmedi"}
        con.execute(
            "INSERT OR IGNORE INTO faaliyet_alanlari(alan_id,ad,holding_key,guncelleme) VALUES(?,?,?,?)",
            ("ALAN-INSAAT", "Insaat Faaliyet Alani", "touche_holding", simdi),
        )
        con.execute(
            "INSERT OR IGNORE INTO sozlesmeler(sozlesme_id,faaliyet_key,alan_id,proje,musteri,tutar_kurus,"
            "para_birimi,durum,olusturan,olusturma) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("SZ-GOKTURK", "gursar_mimarlik", "ALAN-INSAAT", "Gokturk Okulu", "Gokturk Belediyesi",
             3_800_000 * 100, "TL", "AKTIF", "demo", simdi),
        )
        for kalem_id, grup, ad, birim, metraj_mikro, satis, maliyet, butce in kalemler:
            con.execute(
                "INSERT OR IGNORE INTO is_kalemleri(kalem_id,sozlesme_id,faaliyet_key,proje,maliyet_grubu,"
                "is_kalemi,birim,metraj_mikro,satis_birim_fiyati_kurus,maliyet_birim_fiyati_kurus,butce_kurus,"
                "guncelleme) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (kalem_id, "SZ-GOKTURK", "gursar_mimarlik", "Gokturk Okulu", grup, ad, birim,
                 int(metraj_mikro), int(satis * 100), int(maliyet * 100), int(butce * 100), simdi),
            )
    return {"yuklendi": len(kalemler), "sozlesme": "SZ-GOKTURK"}

