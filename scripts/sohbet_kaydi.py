# -*- coding: utf-8 -*-
"""CFO sohbetinden kontrollu finans kaydi taslagi uretir.

Bu modul Excel'e yazmaz. LLM yalnizca alan cikartir; faaliyet, proje, cari,
islem turu ve hesap degerleri master data ile deterministik olarak dogrulanir.
"""

from __future__ import annotations

import ast
import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import date, datetime
from pathlib import Path

from config import FIRMALAR
from dogrulayici import deterministik_dogrula
from guvenlik import audit_yaz, finansal_fingerprint
from master_data import master_data_oku
from ollama_client import metne_sor, metne_sor_yedek, son_metin_saglayici


KAYIT_TERIMLERI = (
    "gelir", "gider", "odeme", "tahsilat", "hakedis", "masraf", "fatura",
    "borc", "alacak", "kredi", "dekont", "cek", "havale", "nakit",
    "transfer", "teminat", "emanet", "taksit", "finansman", "islem",
)
KAYIT_FIILLERI = (
    "ekle", "kaydet", "isle", "gir", "yaz", "olustur", "duzelt",
)
SORGU_KALIPLARI = (
    "ne kadar", "kalem kalem", "listele", "goster", "hangisi", "hangi ",
    "nedir", "var mi", "yapabilir", "yazabilir", "yazdirabilir", "miyiz",
    "misin", "musun", "miyim", "islendi mi", "kaydedildi mi", "eklendi mi",
    "yazildi mi",
)
GUCLU_KAYIT_KALIPLARI = (
    "excele isle", "excel e isle", "kayda al", "kayit olustur", "yeni islem",
    "isleyelim", "kaydedelim", "sisteme ekle", "deftere isle",
)


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _uyari_metni(value):
    """Modelin metin yerine nesne döndürdüğü uyarıları kullanıcı metnine çevirir."""
    if isinstance(value, dict):
        for alan in ("mesaj", "aciklama", "uyari", "not"):
            metin = str(value.get(alan) or "").strip()
            if metin:
                return metin
        return ""
    metin = str(value or "").strip()
    onek = "AI kayıt notu:"
    ham = metin
    if metin.startswith(onek):
        ham = metin[len(onek):].strip()
    if ham.startswith("{") and ham.endswith("}"):
        try:
            nesne = ast.literal_eval(ham)
        except (ValueError, SyntaxError):
            nesne = None
        if isinstance(nesne, dict):
            temiz = _uyari_metni(nesne)
            if temiz:
                return temiz
    return metin


def kayit_niyeti_mi(soru):
    """Rapor sorularini finansal yazma komutlarindan ayirir."""
    sade = _norm(soru)
    if not sade or any(kalip in sade for kalip in SORGU_KALIPLARI):
        return False
    if any(kalip in sade for kalip in GUCLU_KAYIT_KALIPLARI):
        return True
    terim = any(re.search(rf"\b{re.escape(x)}\w*\b", sade) for x in KAYIT_TERIMLERI)
    fiil = any(re.search(rf"\b{re.escape(x)}\w*\b", sade) for x in KAYIT_FIILLERI)
    return terim and fiil


def faaliyet_key_bul(soru, secili="tum"):
    if secili and secili != "tum" and secili in FIRMALAR:
        return secili
    sade = _norm(soru)
    bulunan = []
    for key, firma in FIRMALAR.items():
        adlar = [key.replace("_", " "), firma.get("ad"), firma.get("faaliyet"), *firma.get("faaliyet_aliaslari", [])]
        if any(_norm(ad) and _norm(ad) in sade for ad in adlar):
            bulunan.append(key)
    bulunan = list(dict.fromkeys(bulunan))
    return bulunan[0] if len(bulunan) == 1 else None


def _eslestir(value, options):
    if value in (None, ""):
        return ""
    aranan = _norm(value)
    tam = [x for x in options if _norm(x) == aranan]
    if len(tam) == 1:
        return tam[0]
    kismi = [x for x in options if aranan and (aranan in _norm(x) or _norm(x) in aranan)]
    if len(kismi) == 1:
        return kismi[0]
    puanli = sorted(
        ((SequenceMatcher(None, aranan, _norm(x)).ratio(), x) for x in options),
        reverse=True,
    )
    if puanli and puanli[0][0] >= 0.84 and (len(puanli) == 1 or puanli[0][0] - puanli[1][0] >= 0.08):
        return puanli[0][1]
    return str(value).strip()


def _acik_cari_adaylari(metin):
    """Kullanicinin acikca yazdigi yeni kisi adlarini dar kaliplarla bulur."""
    kaynak = str(metin or "").casefold().replace("'", " ")
    adaylar = []
    kaliplar = (
        r"\bçek\s+([a-zçğıöşü]+(?:\s+[a-zçğıöşü]+){1,3})\s+verildi\b",
        r"\b(?:tl|try|usd|usdt|eur)\s+([a-zçğıöşü]+(?:\s+[a-zçğıöşü]+){1,3})\s+hesab[ıi]na\b",
    )
    for kalip in kaliplar:
        for eslesme in re.findall(kalip, kaynak, flags=re.IGNORECASE):
            kelimeler = eslesme.split()
            if "verildi" in kalip and kelimeler:
                son = kelimeler[-1]
                if len(son) > 4 and son[-1] in "ae":
                    kelimeler[-1] = son[:-1]
            aday = " ".join(x.capitalize() for x in kelimeler).strip()
            if aday and _norm(aday) not in {_norm(x) for x in adaylar}:
                adaylar.append(aday)
    return adaylar


def _islem_turu(raw, metin, izinli):
    eslesen = _eslestir(raw, izinli)
    if any(_norm(x) == _norm(eslesen) for x in izinli):
        return eslesen
    sade = _norm(f"{raw or ''} {metin}")
    aday = ""
    ham = _norm(raw)
    if "hakedis" in sade:
        aday = "Hakediş / Gelir Tahakkuku"
    elif "tahsilat" in sade:
        aday = "Tahsilat"
    elif ("cek" in sade and "teminat" in sade and "borclan" in sade) or "cek karsiligi borc" in sade:
        aday = "Çek Teminatlı Borçlanma"
    elif "kredi kullan" in sade:
        aday = "Kredi Kullanımı"
    elif "kredi" in sade and "taksit" in sade:
        aday = "Kredi Taksit Ödemesi"
    elif "banka" in sade and "masraf" in sade:
        aday = "Banka Masrafı"
    elif "ortak" in sade and "cepten" in sade:
        aday = "Ortak Cepten Gider"
    elif "ortak" in sade and ("odeme" in sade or "odendi" in sade):
        aday = "Ortak Cari Ödemesi"
    elif "havale" in ham or "transfer" in ham:
        aday = "Gider Tahakkuku" if any(x in sade for x in ("yapilacak", "odenecek", "planlan")) else "Usta/Tedarikçi Ödemesi"
    elif "odeme" in sade or "odendi" in sade or "odedim" in sade:
        aday = "Usta/Tedarikçi Ödemesi"
    elif "gider" in sade or "fatura" in sade:
        aday = "Gider Tahakkuku"
    elif "gelir" in sade:
        aday = "Diğer Gelir"
    return _eslestir(aday, izinli) if aday else str(raw or "").strip()


def _bugun_ifadesi(metin):
    sade = _norm(metin)
    return "bugun" in sade or "simdi" in sade


def _tekil_gecmis_degeri(kayitlar, alan):
    degerler = []
    for kayit in kayitlar:
        deger = str(kayit.get(alan) or "").strip()
        if deger and _norm(deger) not in {_norm(x) for x in degerler}:
            degerler.append(deger)
    return degerler[0] if len(degerler) == 1 else ""


def _benzer_kayittan_tamamla(kayit, md):
    """Aynı carinin tekil ve tutarlı geçmiş sınıflandırmasını kullanır.

    Tutar, tarih, kur ve işlem türü geçmişten asla alınmaz. Proje ve hesap
    yalnız bütün uygun geçmiş satırlarında tek bir değer varsa tamamlanır;
    böylece benzer kayıt kolaylık sağlar ama belirsizlik gizlenmez.
    """
    cari = str(kayit.get("Cari") or "").strip()
    if not cari:
        return kayit
    gecmis = [
        x for x in md.get("kayit_gecmisi", [])
        if _norm(x.get("cari")) == _norm(cari)
    ]
    if not gecmis:
        return kayit
    eslesmeler = list(kayit.get("_otomatik_eslesmeler") or [])
    if not kayit.get("Proje"):
        proje = _tekil_gecmis_degeri(gecmis, "proje")
        if proje and any(_norm(x) == _norm(proje) for x in md.get("projeler", [])):
            kayit["Proje"] = proje
            eslesmeler.append(f"Proje geçmiş {cari} kayıtlarından eşleştirildi: {proje}")

    islem = _norm(kayit.get("Islem_Turu"))
    pb = str(kayit.get("PB") or "TL").upper()
    ayni_islem = [
        x for x in gecmis
        if (not islem or _norm(x.get("islem_turu")) == islem)
        and str(x.get("pb") or "TL").upper() == pb
    ]
    if not kayit.get("Hesap_Kasa") and islem:
        hesap = _tekil_gecmis_degeri(ayni_islem, "hesap_kasa")
        if hesap and any(_norm(x) == _norm(hesap) for x in md.get("hesaplar", [])):
            kayit["Hesap_Kasa"] = hesap
            eslesmeler.append(f"Hesap geçmiş {cari} / {kayit.get('Islem_Turu')} kayıtlarından eşleştirildi: {hesap}")
    if eslesmeler:
        kayit["_otomatik_eslesmeler"] = eslesmeler
    return kayit


def _kaydi_tamamla(kayit, metin, firma_key, md):
    kayit = dict(kayit or {})
    kayit["Vade"] = kayit.get("Vade") or kayit.get("Vade_Tarihi") or kayit.get("Vede") or ""
    faaliyetler = md.get("faaliyetler", [])
    if faaliyetler:
        kayit["Faaliyet"] = _eslestir(kayit.get("Faaliyet"), faaliyetler) or faaliyetler[0]
    kayit["Proje"] = _eslestir(kayit.get("Proje"), md.get("projeler", []))
    if not kayit["Proje"] and len(md.get("projeler", [])) == 1:
        kayit["Proje"] = md["projeler"][0]
    ham_cari = str(kayit.get("Cari") or "").strip()
    kaynak_norm = _norm(metin)
    if not ham_cari:
        metinde_gecen_cariler = [x for x in md.get("cariler", []) if _norm(x) and _norm(x) in kaynak_norm]
        if len(metinde_gecen_cariler) == 1:
            ham_cari = metinde_gecen_cariler[0]
    cari_adaylari = _acik_cari_adaylari(metin)
    if ham_cari and _norm(ham_cari) not in kaynak_norm and cari_adaylari:
        aciklama_norm = _norm(kayit.get("Aciklama"))
        aciklamada = [x for x in cari_adaylari if _norm(x) in aciklama_norm]
        if len(aciklamada) == 1:
            ham_cari = aciklamada[0]
        elif len(cari_adaylari) == 1:
            ham_cari = cari_adaylari[0]
    kayit["Cari"] = _eslestir(ham_cari, md.get("cariler", []))
    cari_tanimli = any(_norm(x) == _norm(kayit["Cari"]) for x in md.get("cariler", []))
    if kayit["Cari"] and not cari_tanimli and _norm(kayit["Cari"]) in _norm(metin):
        # Kullanici tarafindan acikca yazilan yeni cari, onay sirasinda master
        # tanimlara eklenecek bir oneridir; modelin uydurdugu adlar bu bayragi alamaz.
        kayit["_yeni_cari"] = True
    kayit["Islem_Turu"] = _islem_turu(kayit.get("Islem_Turu"), metin, md.get("islem_turleri", []))

    cari_tipi = _eslestir(kayit.get("Cari_Tipi"), md.get("cari_tipler", []))
    if kayit.get("Cari"):
        eslesenler = [x for x in md.get("cari_hiyerarsisi", []) if _norm(x.get("cari")) == _norm(kayit["Cari"])]
        if len(eslesenler) == 1:
            cari_tipi = eslesenler[0].get("cari_tipi") or cari_tipi
    if kayit.get("_yeni_cari"):
        kaynak = _norm(metin)
        cari_anahtari = _norm(kayit.get("Cari"))
        acik_tip = next((tip for tip in md.get("cari_tipler", [])
                         if f"{_norm(tip)} {cari_anahtari}" in kaynak
                         or f"{cari_anahtari} {_norm(tip)}" in kaynak), "")
        cari_tipi = acik_tip or "Diğer"
    kayit["Cari_Tipi"] = cari_tipi or ("Diğer" if kayit.get("_yeni_cari") else "")

    pb = str(kayit.get("PB") or "TL").upper().replace("TRY", "TL")
    kayit["PB"] = pb
    if kayit.get("Kur") in (None, ""):
        kayit["Kur"] = md.get("kurlar", {}).get(pb, 1 if pb == "TL" else "")
    hesaplar = md.get("hesaplar", [])
    kayit["Hesap_Kasa"] = _eslestir(kayit.get("Hesap_Kasa"), hesaplar)
    if not kayit["Hesap_Kasa"]:
        kaynak = _norm(metin)
        if "elden" in kaynak:
            pb_hesaplari = [x for x in hesaplar if pb.casefold() in str(x).casefold() and "kasa" in _norm(x)]
        else:
            # Faaliyet adının geçmesi tek başına banka hesabı seçmek için
            # yeterli değildir. Hesabın tam adı açıkça yazılmalı veya aşağıda
            # benzer geçmiş kayıt tekil bir hesap göstermelidir.
            isimli_hesaplar = [x for x in hesaplar if _norm(x) and _norm(x) in kaynak]
            pb_hesaplari = isimli_hesaplar or [x for x in hesaplar if pb.casefold() in str(x).casefold()]
        if len(pb_hesaplari) == 1:
            kayit["Hesap_Kasa"] = pb_hesaplari[0]
    kayit = _benzer_kayittan_tamamla(kayit, md)
    if not kayit.get("Tarih") and _bugun_ifadesi(metin):
        kayit["Tarih"] = date.today().isoformat()
    kayit["Aciklama"] = str(kayit.get("Aciklama") or metin).strip()[:500]
    return kayit


def _is_kurallarini_uygula(veri, metin, md):
    """LLM yorumunu finansal olarak tehlikeli durumlarda deterministik sinirlar."""
    kayitlar = []
    riskler = []
    uyarilar = [_uyari_metni(x) for x in veri.get("uyarilar", [])]
    uyarilar = [x for x in uyarilar if x]
    izinli_islemler = md.get("islem_turleri", [])
    borclanma_turu = _eslestir("Çek Teminatlı Borçlanma", izinli_islemler)
    if not any(_norm(x) == _norm(borclanma_turu) for x in izinli_islemler):
        borclanma_turu = _eslestir("Kredi Kullanımı", izinli_islemler)
    gider_tahakkuku_turu = _eslestir("Gider Tahakkuku", izinli_islemler)
    kaynak = _norm(metin)
    cari_adaylari = _acik_cari_adaylari(metin)
    cek_finansman_carisi = cari_adaylari[0] if cari_adaylari and all(
        x in kaynak for x in ("cek", "karsiliginda", "nakit")
    ) else ""
    for kayit in veri.get("kayitlar", []):
        anlam = _norm(f"{kayit.get('Aciklama', '')} {kayit.get('Islem_Turu', '')}")
        ham_islem = _norm(kayit.get("Islem_Turu"))
        cari_norm = _norm(kayit.get("Cari"))
        kaynakta_planli_havale = bool(
            cari_norm and re.search(
                rf"\b{re.escape(cari_norm)}\s+hesabina\b.{{0,80}}\b(?:havale|transfer)\b.{{0,40}}\b(?:yapilacak|odenecek|gonderilecek)\b",
                kaynak,
            )
        )
        planli_havale = kaynakta_planli_havale or (
            "hesabina" in anlam
            and "havale" in anlam
            and any(x in anlam for x in ("yapilacak", "odenecek", "gonderilecek"))
        )
        if not planli_havale and cek_finansman_carisi and ham_islem in {
            _norm("Çek Alındı"), _norm("Nakit Girişi"), _norm("Kredi Kullanımı")
        }:
            kayit["Cari"] = cek_finansman_carisi
            kayit["Cari_Tipi"] = "Diğer"
            kayit["_yeni_cari"] = True
            if borclanma_turu:
                kayit["Islem_Turu"] = borclanma_turu
                if "cek teminati karsiligi finansman girisi" not in _norm(kayit.get("Aciklama")):
                    kayit["Aciklama"] = (
                        str(kayit.get("Aciklama") or "")
                        + " Çek teminatı karşılığı finansman girişi."
                    ).strip()
        kosullu = any(x in anlam for x in ("ihtimal", "karsiliksiz cikarsa", "durumunda", "eger"))
        rezerv = any(x in anlam for x in ("nakit hazir", "rezerv", "risk", "hazirlanmasi"))
        if kosullu and rezerv:
            risk_notu = {
                "Tarih": kayit.get("Tarih") or kayit.get("Vade") or "",
                "Tutar": kayit.get("Tutar"), "PB": kayit.get("PB") or "TL",
                "Aciklama": kayit.get("Aciklama") or "Koşullu finansal risk",
            }
            riskler.append(risk_notu)
            uyarilar.append(
                f"Koşullu risk gerçek hareket olarak yazılmadı: {risk_notu['Tutar']} {risk_notu['PB']} - {risk_notu['Aciklama']}"
            )
            continue
        cek_karsiligi_nakit = (
            not planli_havale
            and "cek" in kaynak and "karsiliginda" in kaynak and "nakit" in kaynak
            and ("cek" in anlam or _norm(kayit.get("Islem_Turu")) == _norm("Çek Alındı"))
        )
        if cek_karsiligi_nakit and borclanma_turu and _norm(kayit.get("Islem_Turu")) != _norm(borclanma_turu):
            kayit["Islem_Turu"] = borclanma_turu
            if "cek teminati karsiligi finansman girisi" not in _norm(kayit.get("Aciklama")):
                kayit["Aciklama"] = (str(kayit.get("Aciklama") or "") + " Çek teminatı karşılığı finansman girişi.").strip()
        if planli_havale and gider_tahakkuku_turu:
            kayit["Islem_Turu"] = gider_tahakkuku_turu
            kayit["Vade"] = kayit.get("Vade") or kayit.get("Tarih") or ""
            kayit["Aciklama"] = re.sub(
                r"\s*Çek teminatı karşılığı finansman girişi\.?",
                "",
                str(kayit.get("Aciklama") or ""),
                flags=re.IGNORECASE,
            ).strip()
        if _norm(kayit.get("Islem_Turu")) == _norm("Gider Tahakkuku") and not kayit.get("Vade"):
            if any(x in f"{anlam} {kaynak}" for x in ("yapilacak", "odenecek", "planlanan", "havale")):
                kayit["Vade"] = kayit.get("Tarih") or ""
        kayitlar.append(kayit)
    if riskler:
        hedef = next((x for x in kayitlar if _norm(x.get("Islem_Turu")) in {
            _norm("Çek Teminatlı Borçlanma"), _norm("Kredi Kullanımı")
        }), None)
        if hedef:
            for risk in riskler:
                not_metin = (
                    f"Koşullu risk notu: {risk.get('Tarih') or 'tarih belirsiz'} tarihinde "
                    f"{risk.get('Tutar')} {risk.get('PB')} nakit rezervi gerekebilir."
                )
                hedef["Aciklama"] = (str(hedef.get("Aciklama") or "") + " " + not_metin).strip()[:500]
                hedef["Vade"] = hedef.get("Vade") or risk.get("Tarih") or ""
    veri["kayitlar"] = kayitlar
    veri["riskler"] = riskler
    veri["uyarilar"] = list(dict.fromkeys(uyarilar))
    return veri


def _coklu_kayit_bekleniyor(metin):
    ham = str(metin or "").casefold().replace("ı", "i")
    para_ifadeleri = re.findall(
        r"\b\d[\d\s.,]*(?:\s*(?:bin|milyon|milyar))?\s*(?:tl|try|usd|usdt|eur)\b|\d[\d\s.,]*\s*[₺$€]",
        ham,
    )
    return len(para_ifadeleri) >= 2


def _adayi_dogrula(ham, metin, firma_key, md):
    if not isinstance(ham, dict) or not isinstance(ham.get("kayitlar"), list):
        return None
    veri = dict(ham)
    veri["kayitlar"] = [
        _kaydi_tamamla(x, metin, firma_key, md)
        for x in ham.get("kayitlar", []) if isinstance(x, dict)
    ]
    veri = _is_kurallarini_uygula(veri, metin, md)
    veri["_hedef"] = "ana_ledger"
    dogrulama = deterministik_dogrula(veri, firma_key, "sohbet_kaydi", md=md)
    model_uyarilari = [_uyari_metni(x) for x in veri.get("uyarilar", [])]
    model_uyarilari = [x for x in model_uyarilari if x]
    model_uyarilari += [f"Açık soru: {_uyari_metni(x)}" for x in veri.get("acik_sorular", []) if _uyari_metni(x)]
    for uyari in model_uyarilari:
        metin_uyari = f"AI kayıt notu: {uyari}"
        if metin_uyari not in dogrulama["uyarilar"]:
            dogrulama["uyarilar"].append(metin_uyari)
    if model_uyarilari and dogrulama["durum"] == "PASS":
        dogrulama["durum"] = "REVIEW"
        dogrulama["skor"] = min(dogrulama["skor"], 84)
    return veri, dogrulama


def _aday_puani(aday, coklu):
    veri, dogrulama, _ = aday
    durum = {"PASS": 300, "REVIEW": 200, "FAIL": 0}.get(dogrulama.get("durum"), 0)
    adet = len(veri.get("kayitlar", []))
    return durum + int(dogrulama.get("skor", 0)) + min(adet, 6) * 5 - (250 if coklu and adet < 2 else 0)


def taslak_hazirla(metin, firma_key, onceki_kayit=None):
    """LLM ciktisini master data ile sinirlayip onaylanabilir taslak dondurur."""
    metin = str(metin or "").strip()
    if not metin:
        raise ValueError("Kayıt talebi boş olamaz.")
    if firma_key not in FIRMALAR:
        raise ValueError("Kayıt için faaliyet seçilmedi. Gürsar Mimarlık gibi faaliyeti belirtin.")
    md = master_data_oku(firma_key)
    onceki_bolumu = ""
    if isinstance(onceki_kayit, (dict, list)):
        import json
        onceki_bolumu = (
            "\nMEVCUT TASLAK/KAYITLAR (belirtilmeyen kayıt ve alanları aynen koru):\n"
            + json.dumps(onceki_kayit, ensure_ascii=False, default=str)
            + "\n"
        )
    prompt = f"""Aşağıdaki Türkçe finans kayıt talebini muhasebe olaylarına ayır ve yalnızca açıkça verilen bilgileri çıkar.
Asla kişi, proje, tarih, tutar, kur veya işlem uydurma. JSON dışında açıklama yazma.
Her ayrı nakit girişi, nakit çıkışı veya tahakkuk ayrı kayıt olmalı; farklı para birimlerini birleştirme.
Gerçekleşmemiş gelecekteki ödeme talebini ödeme yapılmış gibi gösterme; uygun ise Gider Tahakkuku ve Vade kullan.
Koşullu risk, nakit rezervi veya "çek karşılıksız çıkarsa" ifadesi gerçek hareket değildir; kayda dönüştürme, uyarilar listesine yaz.
Mevcut olduğu söylenen çek/hareketi ikinci kez kaydetme. Bir kişiye verilen çek karşılığında alınan parayı gelir veya banka kredisi sayma; Çek Teminatlı Borçlanma olarak sınıflandır.
Kullanıcıda açıkça geçen fakat izinli cari listesinde olmayan adı aynen koru, Yeni_Cari=true yap ve Cari_Tipi için yalnızca izinli bir değer veya Diğer kullan.
Format: {{"kayitlar":[{{"Tarih":"", "Faaliyet":"", "Proje":"", "Cari_Tipi":"", "Cari":"", "Yeni_Cari":false, "Islem_Turu":"", "Hesap_Kasa":"", "Tutar":"", "PB":"TL", "Kur":"", "Vade":"", "Aciklama":""}}], "uyarilar":[], "acik_sorular":[]}}
İzinli faaliyetler: {', '.join(md['faaliyetler'])}
İzinli projeler: {', '.join(md['projeler'])}
İzinli cari tipleri: {', '.join(md['cari_tipler'])}
İzinli cariler: {', '.join(md['cariler'])}
İzinli işlem türleri: {', '.join(md['islem_turleri'])}
İzinli hesaplar: {', '.join(md['hesaplar'])}
{onceki_bolumu}

KULLANICI TALEBİ:
{metin}"""
    adaylar = []
    try:
        ham = metne_sor(prompt)
        saglayici = son_metin_saglayici()
        aday = _adayi_dogrula(ham, metin, firma_key, md)
        if aday:
            adaylar.append((*aday, saglayici))
    except Exception as exc:
        ilk_hata = exc
    coklu = _coklu_kayit_bekleniyor(metin)
    ilk_yetersiz = not adaylar or (coklu and len(adaylar[0][0].get("kayitlar", [])) < 2)
    if ilk_yetersiz:
        try:
            haric = adaylar[0][2] if adaylar else None
            ham = metne_sor_yedek(prompt, haric=haric)
            saglayici = son_metin_saglayici()
            aday = _adayi_dogrula(ham, metin, firma_key, md)
            if aday:
                adaylar.append((*aday, saglayici))
        except Exception:
            pass
    if not adaylar:
        raise ValueError(f"Yapay zekâ geçerli bir kayıt taslağı üretmedi: {locals().get('ilk_hata', 'model cevabı yok')}")
    veri, dogrulama, saglayici = max(adaylar, key=lambda x: _aday_puani(x, coklu))
    belge_hash = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    belge_id = f"{firma_key[:4].upper()}-CHAT-{datetime.now():%Y%m%d-%H%M%S-%f}"
    for kayit in veri.get("kayitlar", []):
        kayit["_dosya_adi"] = "CFO asistanı sohbet kaydı"
        kayit["_guven"] = dogrulama["skor"] / 100.0
        kayit["_hash"] = belge_hash
        kayit["_fingerprint"] = finansal_fingerprint(kayit, firma_key, "sohbet_kaydi")
        kayit["_dogrulama_durumu"] = dogrulama["durum"]
        kayit["_dogrulama_ozeti"] = "; ".join((dogrulama["hatalar"] + dogrulama["uyarilar"])[:3])
    hazir = {
        "basari": True, "foto_yolu": None, "model_yolu": None,
        "kaynak_metin": metin, "firma_key": firma_key, "firma_guven": 100,
        "tur": "sohbet_kaydi", "tur_guven": dogrulama["skor"], "veri": veri,
        "belge_id": belge_id, "belge_hash": belge_hash, "dogrulama": dogrulama,
        "gecici_dosyalar": [],
        "cikarim_motoru": "evren_api" if saglayici == "evren" else "yerel_ollama",
    }
    audit_yaz("sohbet_kaydi_dogrulama", belge_id=belge_id, firma=firma_key,
               durum=dogrulama["durum"], skor=dogrulama["skor"], sha256=belge_hash)
    return hazir


def dogrulanmis_taslak_hazirla(ham_veri, metin, firma_key, kaynaklar=None):
    """Belgeyle doğrulanan açık kayıtları LLM'e yeniden yorumlatmadan taslaklaştırır.

    Bu yol yalnızca kullanıcı talebi ve dekont gibi kanıtlarla alanları açıkça
    belirlenmiş işlemler içindir; Excel'e yazmaz ve normal onay kuyruğunu kullanır.
    """
    metin = str(metin or "").strip()
    if not metin or firma_key not in FIRMALAR:
        raise ValueError("Doğrulanmış taslak için metin ve faaliyet gerekli.")
    md = master_data_oku(firma_key)
    aday = _adayi_dogrula(ham_veri, metin, firma_key, md)
    if not aday:
        raise ValueError("Doğrulanmış kayıt taslağı geçersiz.")
    veri, dogrulama = aday
    belge_hash = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    belge_id = f"{firma_key[:4].upper()}-CHAT-{datetime.now():%Y%m%d-%H%M%S-%f}"
    kaynak_adi = ", ".join(Path(str(x)).name for x in (kaynaklar or [])) or "CFO asistanı sohbet kaydı"
    for kayit in veri.get("kayitlar", []):
        kayit["_dosya_adi"] = kaynak_adi
        kayit["_guven"] = dogrulama["skor"] / 100.0
        kayit["_hash"] = belge_hash
        kayit["_fingerprint"] = finansal_fingerprint(kayit, firma_key, "sohbet_kaydi")
        kayit["_dogrulama_durumu"] = dogrulama["durum"]
        kayit["_dogrulama_ozeti"] = "; ".join((dogrulama["hatalar"] + dogrulama["uyarilar"])[:3])
    hazir = {
        "basari": True, "foto_yolu": None, "model_yolu": None,
        "kaynak_metin": metin, "kaynaklar": [str(x) for x in (kaynaklar or [])],
        "firma_key": firma_key, "firma_guven": 100,
        "tur": "sohbet_kaydi", "tur_guven": dogrulama["skor"], "veri": veri,
        "belge_id": belge_id, "belge_hash": belge_hash, "dogrulama": dogrulama,
        "gecici_dosyalar": [], "cikarim_motoru": "deterministik_belge_dogrulama",
    }
    audit_yaz("sohbet_kaydi_belge_dogrulama", belge_id=belge_id, firma=firma_key,
               durum=dogrulama["durum"], skor=dogrulama["skor"], sha256=belge_hash,
               kaynak_sayisi=len(kaynaklar or []))
    return hazir
