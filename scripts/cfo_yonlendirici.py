"""CFO sohbet yonlendiricisi (HTTP'den bagimsiz, test edilebilir).

/api/chat mesajini su sirayla ele alir ve ele almazsa ``None`` doner (mevcut hat devam eder):

  1. Onay bekleyen sihirbaz taslagina "Duzelt: ..." -> deterministik duzeltme
  2. Acik sihirbaz sorusuna cevap (metin) / iptal
  3. Yeni mesaj: SORGU -> deterministik okuma sorgusu (Excel/defter DEGISMEZ)
                 KAYIT -> yonlendirmeli taslak (LLM yok); karmasik/kurallı talimat mevcut LLM hattina birakilir

Hicbir noktada Excel'e dogrudan yazilmaz; yazma yalniz sifreli onay kuyrugundan gecer.
"""
from __future__ import annotations

import re

import cfo_intelligence as CI
import cfo_niyet as N
import cfo_sohbet
import cfo_sorgu
import cfo_varliklar
import proje_finansi as pf
import yetki as rol_yetki
from cfo_varliklar import norm


def _aktor(oturum):
    return oturum.get("kimlik") or "yerel_kullanici"


def _kapsam(oturum):
    k = oturum.get("faaliyetler")
    return None if not k or "*" in k else list(k)


def _yetki_yok(mesaj="Kayıt taslağı oluşturma yetkiniz yok."):
    return {"cevap": mesaj, "niyet": "yetkisiz", "llm": "deterministik", "guven": "Yüksek", "kanitlar": [],
            "aksiyonlar": [], "degisiklik_yapildi": False}


def _bekleyen_cfo_taslagi(token):
    """Onay kuyrugundaki bir taslak sihirbaz kaynakliysa taslak kimligini dondurur."""
    import ui_belge_servisi as ui

    kayit = ui._BEKLEYEN.get(token)
    if kayit and (kayit.get("hazir") or {}).get("cikarim_motoru") == cfo_sohbet.MOTOR:
        return kayit["hazir"].get("taslak_id")
    return None


_BOS_BASLANGIC = re.compile(r"(?:bir )?yeni (kayit|islem|odeme|gider|tahsilat|hakedis|gelir)|(?:kayit|islem) ekle")


def _bos_baslangic(sade, ham, katalog, bugun):
    """'yeni ödeme' gibi tutarsiz baslangiclar: bos sihirbaz acilir, her sey sirayla sorulur."""
    m = _BOS_BASLANGIC.fullmatch(sade)
    if not m:
        return None
    kelime = m.group(1) or "kayit"
    n = N.analiz_et(f"{kelime} ekle" if kelime not in ("kayit", "islem") else "kayit ekle", katalog, bugun, zorla=True)
    n.tur, n.metin = "KAYIT", ham
    n.tarih, n.tarih_kaynagi = (bugun or N.date.today()), "varsayilan_bugun"
    n.notlar = ["Tarih belirtilmediği için bugün varsayıldı."]
    return n


def sohbet_isle(body: dict, oturum: dict, rol: str, katalog=None, bugun=None):
    soru = str(body.get("soru") or "").strip()
    sade = norm(soru)
    aktor = _aktor(oturum)
    aktif_token = str(body.get("aktif_taslak") or "").strip()
    cfo_id = str(body.get("cfo_taslak") or "").strip()
    katalog = katalog or cfo_varliklar.katalog_yukle()
    # Çok turlu bağlam yalnızca kullanıcı açıkça "aynı proje/bu cari" dediğinde
    # genişletilir. Değerler katalogla yeniden çözümlenir; kalıcı yazma yoktur.
    context = CI.ConversationContext.from_mapping(body.get("cfo_context"))
    expanded_soru, _context_used = CI.expand_with_context(soru, context)

    # 1) Onay bekleyen sihirbaz taslagini deterministik duzelt
    if aktif_token and sade.startswith("duzelt"):
        taslak_id = _bekleyen_cfo_taslagi(aktif_token)
        if taslak_id:
            if not rol_yetki.izin_var(rol, "belge.yukle"):
                return _yetki_yok()
            return cfo_sohbet.duzelt(taslak_id, soru, aktor, katalog)
        return None

    # 2) Acik sihirbaz sorusu
    if cfo_id:
        try:
            taslak = cfo_sohbet.getir(cfo_id)
        except ValueError:
            taslak = None
        if taslak and taslak["durum"] == cfo_sohbet.TOPLANIYOR and taslak["aktor"] == str(aktor):
            if sade in {"iptal", "vazgec", "vazgectim", "iptal et"}:
                return cfo_sohbet.iptal(cfo_id, aktor)
            yanit = cfo_sohbet.metinle_cevapla(cfo_id, soru, aktor, katalog)
            if not yanit.get("hata"):
                return yanit
            # Cevap alana uymadi; mesaj kendi basina yeni bir talimat olabilir.
            n = N.analiz_et(soru, katalog, bugun)
            if not (n.tur == "KAYIT" and n.islem and n.tutar and not n.karmasik):
                return yanit

    # 3) Yeni mesaj
    bos_baslangic = _bos_baslangic(sade, soru, katalog, bugun)
    if bos_baslangic is not None:
        if not rol_yetki.izin_var(rol, "belge.yukle"):
            return _yetki_yok()
        return cfo_sohbet.baslat(bos_baslangic, aktor, katalog, bugun=bugun, kapsam=_kapsam(oturum))
    n = N.analiz_et(expanded_soru, katalog, bugun)
    n.metin = soru
    if n.tur == "SORGU":
        return cfo_sorgu.cevapla(soru, katalog, bugun, aktor)          # None -> mevcut danisman
    if n.tur != "KAYIT":
        return None
    # Birden fazla finansal işlem içeren mesajlar tek kalıba zorlanmaz.
    # Her maddeyi görünür kıl, eksik alan varsa sor ve mevcut onay akışına
    # geçirilmeden önce kullanıcıdan bağımsız onay bekle. Burada yazma yoktur.
    zeka = CI.analyze_turn(soru, katalog, bugun, context)
    if len(zeka.proposals) > 1:
        satirlar = []
        for sira, teklif in enumerate(zeka.proposals, 1):
            satirlar.append(
                f"{sira}) {teklif.activity or 'Faaliyet eksik'} · "
                f"{teklif.project or 'Proje eksik'} · {teklif.counterparty or 'Cari eksik'} · "
                f"{teklif.amount or 'Tutar eksik'} {teklif.currency or ''}"
            )
        if zeka.clarification:
            cevap = "Mesajı ayrı işlem taslaklarına ayırdım. Önce şu bilgileri netleştirelim:\n- " + "\n- ".join(zeka.clarification)
        else:
            cevap = "Mesajı bağımsız işlem taslaklarına ayırdım. Her satır ayrı doğrulanıp onaylanmalıdır.\n"
        cevap += "\n" + "\n".join(satirlar) + "\nOnaydan önce Excel ve SQLite'a hiçbir şey yazılmadı."
        return {"cevap": cevap, "niyet": "coklu_kayit_taslagi", "llm": "deterministik",
                "guven": "Yüksek", "cfo_intelligence": zeka.as_dict(),
                "kanitlar": [], "aksiyonlar": [], "degisiklik_yapildi": False}
    if n.desteklenmeyen:
        return {"cevap": n.desteklenmeyen + " Excel'e hiçbir şey yazılmadı.", "niyet": "cfo_desteklenmiyor",
                "llm": "deterministik", "guven": "Yüksek", "kanitlar": [], "aksiyonlar": [], "degisiklik_yapildi": False}
    if n.karmasik:
        return None          # birden fazla tutar / vade / cek-kredi kurallari: mevcut LLM destekli hat
    if not rol_yetki.izin_var(rol, "belge.yukle"):
        return _yetki_yok()
    result = cfo_sohbet.baslat(n, aktor, katalog, yine_de=False, bugun=bugun, kapsam=_kapsam(oturum))
    if zeka.safe_to_draft:
        result["cfo_context"] = CI.context_from_result(zeka).as_dict()
    return result


def adim_isle(body: dict, oturum: dict, rol: str, katalog=None, bugun=None):
    """POST /api/cfo/adim: secenek tiklamasi (LLM yok)."""
    if not rol_yetki.izin_var(rol, "belge.yukle"):
        return _yetki_yok()
    aktor = _aktor(oturum)
    katalog = katalog or cfo_varliklar.katalog_yukle()
    alan, secim = str(body.get("alan") or ""), body.get("secim")
    if alan == "tekrar":
        if secim == "yine_de":
            n = N.analiz_et(str(body.get("metin") or ""), katalog, bugun)
            if n.tur != "KAYIT" or n.karmasik or n.desteklenmeyen:
                raise ValueError("Tekrar edilecek talimat geçerli değil.")
            return cfo_sohbet.baslat(n, aktor, katalog, yine_de=True, bugun=bugun, kapsam=_kapsam(oturum))
        return {"cevap": "Vazgeçildi; yeni kayıt oluşturulmadı.", "niyet": "kayit_reddi", "llm": "deterministik",
                "guven": "Yüksek", "kanitlar": [], "aksiyonlar": [], "degisiklik_yapildi": False, "taslak_kapat": True}
    if alan == "iptal":
        return cfo_sohbet.iptal(str(body.get("taslak_id") or ""), aktor)
    return cfo_sohbet.cevapla(str(body.get("taslak_id") or ""), alan or None, secim, aktor, "secim", katalog)


def veri_isle(yol: str, q: dict, oturum: dict, kapsam_izinli, katalog=None):
    """GET veri uclari (faaliyet/proje/holding detaylari). Kapsam disi istek PermissionError verir."""
    def p(ad):
        return (q.get(ad) or [None])[0]

    katalog = katalog or cfo_varliklar.katalog_yukle()
    if yol == "/api/holding-ozeti":
        if not kapsam_izinli(oturum, None):
            raise PermissionError("Konsolide rapor için yetkiniz yok.")
        return pf.holding_ozeti(katalog)
    if yol == "/api/faaliyet-detay":
        key = p("faaliyet")
        if not kapsam_izinli(oturum, key):
            raise PermissionError("Bu faaliyet için yetkiniz yok.")
        return pf.faaliyet_detay(key, katalog)
    if yol == "/api/proje-listesi":
        key = p("faaliyet")
        if key and not kapsam_izinli(oturum, key):
            raise PermissionError("Bu faaliyet için yetkiniz yok.")
        yanit = pf.proje_listesi(key, katalog)
        if not key:
            yanit["projeler"] = [x for x in yanit["projeler"] if kapsam_izinli(oturum, x["faaliyet_key"])]
        return yanit
    if yol == "/api/proje-detay":
        key = p("faaliyet")
        if not kapsam_izinli(oturum, key):
            raise PermissionError("Bu faaliyet için yetkiniz yok.")
        return pf.proje_detay(key, p("proje") or "", katalog)
    if yol == "/api/cfo/taslak":
        t = cfo_sohbet.aktif_taslak(_aktor(oturum))
        if not t:
            return {"taslak": None}
        return {"taslak": cfo_sohbet.devam(t["id"], katalog)}
    if yol == "/api/kayit-gecmisi":
        return cfo_sorgu.kayit_gecmisi(str(p("kimlik") or ""))
    return None
