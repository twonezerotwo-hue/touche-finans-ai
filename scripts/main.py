# -*- coding: utf-8 -*-
"""Ana giris noktasi: Belge -> firma tani -> tur tani -> icerik -> dogrula -> Excel"""

import argparse
import hashlib
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import GELEN_EVRAK, ARSIV, ARSIV_HATA, LOGLAR, FIRMALAR
from firma_taniyici import tani as firma_tani
from belge_turleri import turleri_getir, tur_bilgisi
from ollama_client import modele_sor, metne_sor
from dogrulayici import deterministik_dogrula, FAIL, REVIEW
from excel_yazici import yaz
from guvenlik import audit_yaz, belge_sha256, finansal_fingerprint
from belge_dosyasi import BELGE_UZANTILARI, gecicileri_temizle, model_gorseli_hazirla
from master_data import master_data_oku
from invoice2data_adapter import belgeyi_oku as invoice2data_oku
from finans_cekirdegi import fis_taslaklari, finans_kaydi_kuyruga_al, finans_kaydi_yaz

log_dosya = LOGLAR / f"{datetime.now():%Y-%m-%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_dosya, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

FOTO_UZANTI = BELGE_UZANTILARI


def _belge_turu_tani(foto_yolu, firma_key):
    turler = turleri_getir(firma_key)
    if not turler:
        return None, 0

    liste = "\n".join([f"- {k}: {v['ad']}" for k, v in turler.items()])

    prompt = (
        "Bu gorsel bir belge fotografi. Asagidaki turlerden HANGISI oldugunu belirle:\n\n"
        + liste
        + "\n\nSADECE su formatta cevap ver:\n"
        + '{"tur": "<tur_anahtari>", "guven": <0-100>}\n\n'
        + "Aciklama yazma, sadece JSON."
    )

    try:
        veri = modele_sor(foto_yolu, prompt)
        tur = veri.get("tur", "").strip().lower()
        guven = int(veri.get("guven", 0))

        if tur not in turler:
            for k in turler.keys():
                if k in tur or tur in k:
                    return k, guven
            return None, 0
        return tur, guven
    except Exception as e:
        log.warning(f"Belge turu tanima hatasi: {e}")
        return None, 0


def _basit_hash(foto_yolu):
    """Geriye uyumlu ad; artik tam SHA-256 dondurur."""
    return belge_sha256(foto_yolu)


def _arsive_tasi(foto_yolu, firma_key, tur):
    gun = datetime.now().strftime("%Y-%m-%d")
    hedef = ARSIV / gun / firma_key
    hedef.mkdir(parents=True, exist_ok=True)
    yeni_ad = f"{tur}_{datetime.now():%H%M%S_%f}_{foto_yolu.name}"
    shutil.move(str(foto_yolu), str(hedef / yeni_ad))
    log.info(f"  Arsiv: {hedef.name}/{yeni_ad}")


def _hataya_tasi(foto_yolu, sebep):
    ARSIV_HATA.mkdir(parents=True, exist_ok=True)
    yeni_ad = f"{sebep}_{datetime.now():%H%M%S_%f}_{foto_yolu.name}"
    shutil.move(str(foto_yolu), str(ARSIV_HATA / yeni_ad))
    log.warning(f"  Hata klasorune: {yeni_ad}")
    return False


def belge_hazirla(foto_yolu, manuel_firma=None, manuel_tur=None, kaynak_yolu=None):
    """AI okuma ve deterministik dogrulama yapar; Excel'e yazmaz."""
    foto_yolu = Path(foto_yolu)
    kaynak_yolu = Path(kaynak_yolu) if kaynak_yolu else foto_yolu
    log.info("=" * 60)
    log.info(f"Isleniyor: {kaynak_yolu.name}")
    if not foto_yolu.exists() or not kaynak_yolu.exists():
        return {"basari": False, "mesaj": f"Dosya bulunamadi: {kaynak_yolu}"}

    belge_hash = belge_sha256(kaynak_yolu)
    audit_yaz("belge_alindi", dosya=kaynak_yolu.name, sha256=belge_hash)
    if manuel_firma and manuel_firma in FIRMALAR:
        firma_key, firma_guven, firma_kaynak = manuel_firma, 100, "manuel"
    else:
        firma_key, firma_guven, firma_kaynak = firma_tani(foto_yolu)
    log.info(f"  Firma ({firma_kaynak}): {FIRMALAR[firma_key]['ad']} (%{firma_guven})")

    if manuel_tur:
        tur, tur_guven = manuel_tur, 100
    else:
        tur, tur_guven = _belge_turu_tani(foto_yolu, firma_key)
        if not tur or tur_guven < 40:
            audit_yaz("belge_hazirlama_hatasi", dosya=kaynak_yolu.name, sebep="tur_taninamadi")
            return {"basari": False, "mesaj": "Belge turu taninamadi.", "foto_yolu": kaynak_yolu}
    log.info(f"  Tur: {tur} (%{tur_guven})")

    bilgi = tur_bilgisi(firma_key, tur)
    if not bilgi:
        return {"basari": False, "mesaj": "Bu belge turu icin islem tanimi yok.", "foto_yolu": kaynak_yolu}

    veri = None
    cikarim_motoru = "qwen2.5vl"
    cikarim_sablonu = ""
    prompt = bilgi['prompt']
    if FIRMALAR[firma_key].get('adapter') == 'standart_v1':
        import json
        prompt += '\nSeçili faaliyet master verisi: ' + json.dumps(master_data_oku(firma_key), ensure_ascii=False)

    # Faturalarda once tedarikciye ozel, deterministik invoice2data sablonlarini
    # deneriz. Eslesmeyen/eksik sonuc mevcut Qwen akisina sessizce geri doner.
    invoice_sonucu = invoice2data_oku(kaynak_yolu, firma_key, tur)
    if invoice_sonucu.get("eslesti"):
        aday = invoice_sonucu["veri"]
        on_kontrol = deterministik_dogrula(aday, firma_key, tur)
        if on_kontrol["durum"] != FAIL:
            veri = aday
            cikarim_motoru = "invoice2data"
            cikarim_sablonu = invoice_sonucu.get("sablon", "")
            audit_yaz(
                "invoice2data_sonucu", dosya=kaynak_yolu.name, firma=firma_key,
                tur=tur, durum="eslesti", sablon=cikarim_sablonu,
                ozet_kaydi=bool(invoice_sonucu.get("ozet_kaydi")),
            )
        else:
            audit_yaz(
                "invoice2data_sonucu", dosya=kaynak_yolu.name, firma=firma_key,
                tur=tur, durum="qwen_yedegi", sebep="deterministik_on_kontrol_fail",
                hatalar=on_kontrol["hatalar"][:5],
            )
    elif tur == "fatura":
        audit_yaz(
            "invoice2data_sonucu", dosya=kaynak_yolu.name, firma=firma_key,
            tur=tur, durum="qwen_yedegi", sebep=invoice_sonucu.get("sebep", "bilinmiyor"),
        )

    if veri is None:
        for deneme in range(2):
            try:
                veri = modele_sor(foto_yolu, prompt)
                break
            except Exception as e:
                log.warning(f"  LLM deneme {deneme+1} basarisiz: {e}")
    if veri is None:
        return {"basari": False, "mesaj": "AI modeli cevap vermedi. Lutfen tekrar deneyin.", "foto_yolu": kaynak_yolu}

    dogrulama = deterministik_dogrula(veri, firma_key, tur)
    if (cikarim_motoru == "invoice2data" and
            veri.get("_cikarim", {}).get("ozet_kaydi") and
            dogrulama["durum"] != FAIL):
        uyari = "invoice2data kalem detayi bulamadi; fatura toplami kullanici tarafindan kontrol edilmeli."
        if uyari not in dogrulama["uyarilar"]:
            dogrulama["uyarilar"].append(uyari)
        dogrulama["durum"] = REVIEW
        dogrulama["skor"] = min(dogrulama["skor"], 84)
    for uyari in dogrulama["uyarilar"]:
        log.warning(f"    UYARI: {uyari}")
    for hata in dogrulama["hatalar"]:
        log.error(f"    HATA: {hata}")
    if dogrulama["durum"] == FAIL:
        mesaj = "\n".join(dogrulama["hatalar"][:8]) or "Deterministik dogrulama basarisiz."
        audit_yaz("dogrulama_sonucu", dosya=kaynak_yolu.name, firma=firma_key, tur=tur,
                   durum=FAIL, skor=dogrulama["skor"], hatalar=dogrulama["hatalar"], sha256=belge_hash)
        return {"basari": False, "mesaj": mesaj, "foto_yolu": kaynak_yolu, "dogrulama": dogrulama}

    belge_id = f"{firma_key[:4].upper()}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    ozet = "; ".join(dogrulama["uyarilar"][:3])
    for kayit in veri.get("kayitlar", []):
        kayit["_dosya_adi"] = kaynak_yolu.name
        kayit["_guven"] = tur_guven / 100.0
        kayit["_hash"] = belge_hash
        kayit["_fingerprint"] = finansal_fingerprint(kayit, firma_key, tur)
        kayit["_dogrulama_durumu"] = dogrulama["durum"]
        kayit["_dogrulama_ozeti"] = ozet
        kayit["_cikarim_motoru"] = cikarim_motoru

    hazir = {"basari": True, "foto_yolu": kaynak_yolu, "model_yolu": foto_yolu, "firma_key": firma_key,
             "firma_guven": firma_guven, "tur": tur, "tur_guven": tur_guven,
             "veri": veri, "belge_id": belge_id, "belge_hash": belge_hash,
             "dogrulama": dogrulama, "cikarim_motoru": cikarim_motoru,
             "cikarim_sablonu": cikarim_sablonu}
    audit_yaz("dogrulama_sonucu", dosya=kaynak_yolu.name, belge_id=belge_id, firma=firma_key,
               tur=tur, durum=dogrulama["durum"], skor=dogrulama["skor"],
               uyarilar=dogrulama["uyarilar"], sha256=belge_hash,
               cikarim_motoru=cikarim_motoru, cikarim_sablonu=cikarim_sablonu,
               fingerprints=[k["_fingerprint"] for k in veri.get("kayitlar", [])])
    return hazir


def belge_dosyasi_hazirla(dosya_yolu, manuel_firma=None, manuel_tur=None):
    """Resim veya PDF dosyasini hazirlar; PDF gecici gorsellerini takip eder."""
    dosya_yolu = Path(dosya_yolu)
    try:
        model_yolu, geciciler = model_gorseli_hazirla(dosya_yolu)
    except Exception as exc:
        return {"basari": False, "mesaj": str(exc), "foto_yolu": dosya_yolu}
    hazir = belge_hazirla(model_yolu, manuel_firma, manuel_tur, kaynak_yolu=dosya_yolu)
    if hazir.get("basari"):
        hazir["gecici_dosyalar"] = [str(p) for p in geciciler]
    else:
        gecicileri_temizle(geciciler)
    return hazir


def metin_hazirla(metin, firma_key):
    """Telegram serbest metnini finans kaydina cevirir; Excel'e yazmaz."""
    metin = str(metin or "").strip()
    if not metin:
        return {"basari": False, "mesaj": "Bos metin kayda donusturulemez."}
    if firma_key not in FIRMALAR:
        return {"basari": False, "mesaj": f"Bilinmeyen firma: {firma_key}"}

    try:
        md = master_data_oku(firma_key)
    except Exception:
        md = None

    if firma_key == "gursar_mimarlik":
        tur = "odeme_dekontu"
        sema = """Tarih, Saat, Is_Kalemi, Cari, Islem_Turu, Tutar, Banka_Masrafi,
Islem_Kanali, FAST_Sorgu_No, Proje ve Maliyet_Grubu alanlarini cikar.
Islem_Turu yalnizca: Usta ödemesi, Ortak cari ödemesi, Banka masrafı, Diğer.
Maliyet_Grubu yalnizca: Taş işleri, Saha betonu, Ortak cari, Diğer."""
    else:
        tur = "banka_dekontu"
        sema = """Faaliyet, Proje, Cari_Tipi, Cari, Islem_Turu, Hesap_Kasa, Tutar,
PB, Kur, Tarih, Vade ve Aciklama alanlarini cikar. Belirtilmeyen alanlari bos birak;
asla tahmin ederek faaliyet, proje veya cari uydurma."""
        if md:
            sema += (
                "\nIzin verilen faaliyetler: " + ", ".join(md["faaliyetler"]) +
                "\nIzin verilen projeler: " + ", ".join(md["projeler"]) +
                "\nIzin verilen cari tipleri: " + ", ".join(md["cari_tipler"]) +
                "\nIzin verilen islem turleri: " + ", ".join(md["islem_turleri"]) +
                "\nIzin verilen hesaplar: " + ", ".join(md["hesaplar"])
            )

    prompt = f"""Asagidaki Turkce finans talebini JSON kaydina donustur.
Firma: {FIRMALAR[firma_key]['ad']}
{sema}
Format kesinlikle: {{"kayitlar":[{{...}}]}}
Sayilari JSON sayisi olarak yaz. Aciklama ekleme.

KULLANICI METNI:
{metin}"""
    try:
        veri = metne_sor(prompt)
    except Exception as exc:
        return {"basari": False, "mesaj": f"AI metni okuyamadi: {exc}"}

    dogrulama = deterministik_dogrula(veri, firma_key, tur, md=md)
    if dogrulama["durum"] == FAIL:
        return {"basari": False, "mesaj": "\n".join(dogrulama["hatalar"][:8]), "dogrulama": dogrulama}

    belge_hash = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    belge_id = f"{firma_key[:4].upper()}-TXT-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    if firma_key == "touche_holding":
        veri["_hedef"] = "ana_ledger"
    for kayit in veri.get("kayitlar", []):
        kayit["_dosya_adi"] = "Telegram metin"
        kayit["_guven"] = dogrulama["skor"] / 100.0
        kayit["_hash"] = belge_hash
        kayit["_fingerprint"] = finansal_fingerprint(kayit, firma_key, tur)
        kayit["_dogrulama_durumu"] = dogrulama["durum"]
        kayit["_dogrulama_ozeti"] = "; ".join(dogrulama["uyarilar"][:3])
    hazir = {
        "basari": True, "foto_yolu": None, "model_yolu": None,
        "kaynak_metin": metin, "firma_key": firma_key, "firma_guven": 100,
        "tur": tur, "tur_guven": dogrulama["skor"], "veri": veri,
        "belge_id": belge_id, "belge_hash": belge_hash, "dogrulama": dogrulama,
        "gecici_dosyalar": [],
    }
    audit_yaz("metin_dogrulama", belge_id=belge_id, firma=firma_key,
               durum=dogrulama["durum"], skor=dogrulama["skor"], sha256=belge_hash)
    return hazir


def hazirlanan_belgeyi_yaz(hazir, onay_kaynagi="otomatik"):
    """Dogrulanmis belgeyi guvenli Excel katmanina gonderir."""
    if not hazir.get("basari"):
        return False, hazir.get("mesaj", "Belge hazir degil.")
    for kayit in hazir["veri"].get("kayitlar", []):
        kayit["_kullanici_onayi"] = "Onaylandı"
        kayit["_onay_kaynagi"] = onay_kaynagi
    try:
        fis_taslaklari(hazir)
    except Exception as exc:
        audit_yaz("muhasebe_fisi_reddi", belge_id=hazir.get("belge_id"), hata=str(exc))
        return False, f"Muhasebe kontrolü başarısız: {exc}"
    sonuc = yaz(hazir["firma_key"], hazir["veri"], hazir["belge_id"])
    if not sonuc["basari"]:
        audit_yaz("belge_isleme_hatasi", belge_id=hazir["belge_id"], hata=sonuc["hata"])
        return False, sonuc["hata"]
    son_hata = None
    for deneme in range(3):
        try:
            fis_sonucu = finans_kaydi_yaz(hazir, onay_kaynagi)
            son_hata = None
            break
        except Exception as exc:
            son_hata = exc
            if deneme < 2:
                time.sleep(0.2 * (deneme + 1))
    if son_hata is not None:
        finans_kaydi_kuyruga_al(hazir, onay_kaynagi, son_hata)
        fis_sonucu = {"fis_sayisi": 0}
        audit_yaz("muhasebe_fisi_kuyrukta", belge_id=hazir["belge_id"], hata=str(son_hata))
    foto_yolu = hazir.get("foto_yolu")
    if foto_yolu and Path(foto_yolu).exists():
        _arsive_tasi(Path(foto_yolu), hazir["firma_key"], hazir["tur"])
    gecicileri_temizle(hazir.get("gecici_dosyalar"))
    audit_yaz("belge_tamamlandi", belge_id=hazir["belge_id"], firma=hazir["firma_key"],
               tur=hazir["tur"], yazilan=sonuc["yazilan"],
               holding_yazilan=sonuc.get("holding_yazilan", 0), onay_kaynagi=onay_kaynagi,
               muhasebe_fisi= fis_sonucu.get("fis_sayisi", 0))
    return True, ""


def belgeyi_reddet(hazir, sebep="kullanici_reddi"):
    foto_degeri = hazir.get("foto_yolu")
    foto_yolu = Path(foto_degeri) if foto_degeri else None
    audit_yaz("belge_reddedildi", belge_id=hazir.get("belge_id"),
               dosya=foto_yolu.name if foto_yolu else "Telegram metin", sebep=sebep)
    if foto_yolu and foto_yolu.exists():
        _hataya_tasi(foto_yolu, sebep)
    gecicileri_temizle(hazir.get("gecici_dosyalar"))


def tek_foto_isle(foto_yolu, manuel_firma=None, manuel_tur=None):
    """Eski CLI davranisini koruyan hazirla + otomatik yaz sarmalayicisi."""
    hazir = belge_dosyasi_hazirla(foto_yolu, manuel_firma, manuel_tur)
    if not hazir.get("basari"):
        if Path(foto_yolu).exists():
            _hataya_tasi(Path(foto_yolu), "dogrulama_hatasi")
        return False, hazir.get("mesaj", "Belge islenemedi.")
    return hazirlanan_belgeyi_yaz(hazir, onay_kaynagi="cli_otomatik")


def main():
    parser = argparse.ArgumentParser(description="Belge fotograflarini Excel'e yazar.")
    parser.add_argument("yol", help="Fotograf dosyasi veya klasor yolu")
    parser.add_argument("--firma", choices=list(FIRMALAR.keys()), default=None)
    parser.add_argument("--tur", default=None)
    args = parser.parse_args()

    hedef = Path(args.yol)
    if not hedef.exists():
        log.error(f"Yol bulunamadi: {hedef}")
        sys.exit(1)

    if hedef.is_file():
        if hedef.suffix.lower() not in FOTO_UZANTI:
            log.error(f"Desteklenmeyen uzanti: {hedef.suffix}")
            sys.exit(1)
        basarili, _ = tek_foto_isle(hedef, args.firma, args.tur)
        sys.exit(0 if basarili else 1)

    fotolar = sorted([p for p in hedef.iterdir()
                      if p.is_file() and p.suffix.lower() in FOTO_UZANTI])
    if not fotolar:
        log.warning(f"Klasorde fotograf yok: {hedef}")
        sys.exit(0)

    log.info(f"Toplam {len(fotolar)} fotograf islenecek.")
    basarili = 0
    basarisiz = 0
    for f in fotolar:
        sonuc, _ = tek_foto_isle(f, args.firma, args.tur)
        if sonuc:
            basarili += 1
        else:
            basarisiz += 1

    log.info(f"Ozet: {basarili} basarili, {basarisiz} basarisiz.")


if __name__ == "__main__":
    main()
