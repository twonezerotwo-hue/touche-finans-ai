# -*- coding: utf-8 -*-
"""CFO asistani: serbest Turkce talimati yapisal niyete cevirir (LLM'siz, deterministik).

Yapay zeka ne yazilacagina karar vermez. Bu modul metinden tutar, para birimi, tarih,
islem turu ve varliklari (faaliyet/proje/cari/is kalemi/hesap) cikarir; belirsizse
``belirsiz``/``oneri`` olarak isaretler. Karmasik veya kuralli talimatlar (birden cok
tutar, ileri vade, cek/kredi vb.) ``karmasik=True`` ile mevcut LLM'li hatta birakilir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from cfo_varliklar import BELIRSIZ, ONERI, TEKIL, YOK, Cozum, Katalog, Varlik, _ek_eslesme, coz, kismi_adaylar, kismi_coz, norm
from dogrulayici import _tutar_cevir

# --- Islem tipleri (kullanici dili -> iç tip) ---
PAYMENT = "PAYMENT"
COUNTERPARTY_PAYMENT = "COUNTERPARTY_PAYMENT"
WORK_ITEM_PAYMENT = "WORK_ITEM_PAYMENT"
EXPENSE = "EXPENSE"
PROJECT_COST = "PROJECT_COST"
PAYABLE = "PAYABLE"
INCOME = "INCOME"
RECEIVABLE = "RECEIVABLE"
PROJECT_REVENUE = "PROJECT_REVENUE"
COLLECTION = "COLLECTION"
RECEIVABLE_ADJUSTMENT = "RECEIVABLE_ADJUSTMENT"
TRANSFER = "TRANSFER"
CORRECTION = "CORRECTION"
REVERSAL = "REVERSAL"
QUERY = "QUERY"

# Soru olarak sunulan, kullanici dostu islem secenekleri (sirayla)
ISLEM_SECENEKLERI = [
    (COUNTERPARTY_PAYMENT, "Cari ödemesi", "Bir kişiye/firmaya yapılan ödeme"),
    (WORK_ITEM_PAYMENT, "İş kalemi ödemesi", "Projenin bir iş kalemi için yapılan ödeme"),
    (EXPENSE, "Gider", "Gider tahakkuku (henüz ödenmemiş maliyet)"),
    (COLLECTION, "Tahsilat", "Müşteriden alınan para"),
    (RECEIVABLE_ADJUSTMENT, "Alacak silme / düzeltme", "Tahsil edilmeyecek veya mahsup edilen alacak (kasa/banka hareketi yok)"),
    (RECEIVABLE, "Hakediş / gelir tahakkuku", "Kazanılan ama henüz tahsil edilmemiş gelir"),
    (INCOME, "Diğer gelir (nakit)", "Doğrudan kasa/bankaya giren gelir"),
]
ETIKET = {t: e for t, e, _ in ISLEM_SECENEKLERI}
ETIKET.update({PAYMENT: "Cari ödemesi", PROJECT_COST: "Proje gideri", PAYABLE: "Borç / gider tahakkuku",
               PROJECT_REVENUE: "Proje geliri", TRANSFER: "Transfer", CORRECTION: "Düzeltme",
               REVERSAL: "Ters kayıt", QUERY: "Sorgu"})

# Islem tipi -> calisma kitabindaki Islem_Turu ADAYLARI (yalniz listede VARSA kullanilir)
ISLEM_TURU_ADAYLARI = {
    COUNTERPARTY_PAYMENT: ["Usta/Tedarikçi Ödemesi"],
    PAYMENT: ["Usta/Tedarikçi Ödemesi"],
    WORK_ITEM_PAYMENT: ["Usta/Tedarikçi Ödemesi"],
    EXPENSE: ["Gider Tahakkuku"],
    PROJECT_COST: ["Gider Tahakkuku"],
    PAYABLE: ["Gider Tahakkuku"],
    COLLECTION: ["Tahsilat"],
    RECEIVABLE_ADJUSTMENT: ["Alacak Düzeltme / Mahsup"],
    RECEIVABLE: ["Hakediş / Gelir Tahakkuku"],
    PROJECT_REVENUE: ["Hakediş / Gelir Tahakkuku"],
    INCOME: ["Diğer Gelir"],
}
ORTAK_ODEME = "Ortak Cari Ödemesi"
# Dogrulayici ile ayni kural: bu islem turlerinde para hareketi oldugundan hesap/kasa zorunlu
NAKIT_ANAHTARLARI = ("tahsilat", "odemesi", "banka masraf", "kredi kullanimi", "cek teminatli borclanma",
                     "intercompany", "diger gelir")

KOMUT_FIILLERI = ("ekle", "kaydet", "yaz", "gir", "isle", "olustur", "yapildi", "yapilmistir")
SORGU_IPUCLARI = ("ne kadar", "kac tl", "kac lira", "kactir", "kaca", "kac ", "toplam ne", "listele", "goster",
                  "nedir", "neydi", "ne zaman", "var mi", "yok mu", "kalem kalem", "ozet", "rapor", "hangisi",
                  "hangi ", "kimler", "neden", "niye", "ne oldu", "kalmis", "kaldi", "kazandik", "kazandim")
LISTE_SORGU_FIILLERI = ("yaz", "listele", "goster", "sirala", "dok", "cikar", "ver")
FINANS_NESNELERI = ("odeme", "odemeler", "tahsilat", "gider", "gelir", "alacak", "borc", "kayit", "hareket")
GELECEK_ANAHTARLARI = ("odenecek", "yapilacak", "yapilmasi", "gonderilecek", "planlan", "vade", "ileri tarih",
                       "gelecek ay", "haftaya", "yarin", "ay sonu", "ayin sonu", "taksit", "yapacagiz")
KURALLI_ANAHTARLAR = ("cek ", " cek", "teminat", "borclan", "kredi", "emanet", "finansman", "ortak cepten",
                      "karsiligi", "rezerv", "ihtimal", "eger ", "durumunda", "senet", "faiz")
AYLAR = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7,
         "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}

_HARF = "a-zA-ZçğıöşüÇĞİÖŞÜ"
_PARA_RE = re.compile(
    r"(?P<sayi>\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)"
    rf"(?:\s*(?P<carpan>bin|milyon|milyar|k)(?![{_HARF}]))?"
    rf"(?:\s*(?P<pb>tl|try|₺|lira|usd|\$|dolar|eur|€|euro|usdt)(?![{_HARF}]))?",
    re.IGNORECASE,
)
_TARIH_RE = [
    (re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b"), "dmy"),
    (re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
]
_AY_TARIH_RE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(AYLAR) + r")(?:\s+(20\d{2}))?\b")
PB_KOD = {"tl": "TL", "try": "TL", "₺": "TL", "lira": "TL", "usd": "USD", "$": "USD", "dolar": "USD",
          "eur": "EUR", "€": "EUR", "euro": "EUR", "usdt": "USDT"}
BIRIM_KELIMELER = ("gun", "ay", "adet", "kez", "saat", "kayit", "hafta", "yil", "kisi", "tane", "metre", "m2")


@dataclass
class Niyet:
    tur: str = "BELIRSIZ"                       # KAYIT | SORGU | BELIRSIZ
    islem: str | None = None
    metin: str = ""
    tutar: Decimal | None = None
    pb: str | None = None
    tarih: date | None = None
    tarih_kaynagi: str = ""                     # metin | varsayilan_bugun
    tarih_hatasi: str = ""
    faaliyet: Varlik | None = None
    proje: Varlik | None = None
    kalem: Varlik | None = None
    cari: Varlik | None = None
    hesap: Varlik | None = None
    yeni_cari_adi: str = ""
    ortak_odemesi: bool = False
    belirsiz: dict = field(default_factory=dict)     # alan -> [Varlik]
    oneri: dict = field(default_factory=dict)        # alan -> [Varlik] (yazim benzerligi; onay ister)
    notlar: list = field(default_factory=list)       # denetim notlari (cikarimlar)
    karmasik: bool = False
    karmasik_nedeni: str = ""
    desteklenmeyen: str = ""                         # TRANSFER/CORRECTION/REVERSAL aciklamasi
    coklu_tutar: bool = False


# ---------------------------------------------------------------------------
# Tutar / para birimi / tarih
# ---------------------------------------------------------------------------

def tarih_cikar(metin, bugun: date | None = None):
    """(tarih, (bas, son), kaynak_hata) doner. Gecmis odakli: yilsiz tarih bugunden ileri ise onceki yil."""
    bugun = bugun or date.today()
    ham = str(metin or "")
    sade = norm(ham)
    for ifade, fark in (("evvelsi gun", 2), ("onceki gun", 2), ("dun", 1), ("bugun", 0)):
        if re.search(rf"\b{ifade}\b", sade):
            return bugun - timedelta(days=fark), None, ""
    kucuk = ham.casefold().replace("ı", "i")
    for kalip, sira in _TARIH_RE:
        m = kalip.search(kucuk)
        if m:
            a, b, c = map(int, m.groups())
            g, ay, yil = (a, b, c) if sira == "dmy" else (c, b, a)
            try:
                return date(yil, ay, g), m.span(), ""
            except ValueError:
                return None, m.span(), f"Geçersiz tarih: {m.group(0)}"
    m = _AY_TARIH_RE.search(norm(ham))
    if m:
        gun_metni, ay_adi, yil_metni = m.groups()
        g = int(gun_metni)
        yil_i = int(yil_metni) if yil_metni else bugun.year
        try:
            sonuc = date(yil_i, AYLAR[ay_adi], g)
        except ValueError:
            return None, None, f"Geçersiz tarih: {m.group(0)}"
        if not yil_metni and sonuc > bugun:
            try:
                sonuc = date(yil_i - 1, AYLAR[ay_adi], g)
            except ValueError:
                return None, None, f"Geçersiz tarih: {m.group(0)}"
        return sonuc, None, ""
    return None, None, ""


def _tarih_aralik_temizle(metin):
    """Tarih ifadelerini tutar taramasindan once bosluga cevirir."""
    sonuc = str(metin or "")
    for kalip, _ in _TARIH_RE:
        sonuc = kalip.sub(" ", sonuc)
    return re.sub(r"\b\d{1,2}\s+(?:%s)(?:\s+20\d{2})?\b" % "|".join(
        ["ocak", "şubat", "subat", "mart", "nisan", "mayıs", "mayis", "haziran", "temmuz", "ağustos", "agustos",
         "eylül", "eylul", "ekim", "kasım", "kasim", "aralık", "aralik"]), " ", sonuc, flags=re.IGNORECASE)


def tutar_cikar(metin):
    """Metindeki para ifadelerini dondurur: [{'tutar': Decimal, 'pb': 'TL'|None, 'guclu': bool}]."""
    temiz = _tarih_aralik_temizle(metin)
    bulunanlar = []
    for m in _PARA_RE.finditer(temiz):
        ham_sayi, carpan, pb = m.group("sayi"), m.group("carpan"), m.group("pb")
        sonrasi = norm(temiz[m.end():m.end() + 14]).split()
        if not (pb or carpan) and ("%" in temiz[m.end():m.end() + 2] or (sonrasi and sonrasi[0] in BIRIM_KELIMELER)):
            continue
        guclu = bool(pb or carpan or re.search(r"\d[.\s]\d{3}", ham_sayi))
        ifade = f"{ham_sayi} {carpan or ''}"
        tutar = _tutar_cevir(ifade)
        if tutar is None or tutar <= 0:
            continue
        bulunanlar.append({"tutar": tutar, "pb": PB_KOD.get((pb or "").casefold()), "guclu": guclu})
    guclu_olanlar = [b for b in bulunanlar if b["guclu"]]
    return guclu_olanlar or bulunanlar


# ---------------------------------------------------------------------------
# Islem turu
# ---------------------------------------------------------------------------

def _icerir(sade, anahtarlar):
    return any(a in sade for a in anahtarlar)


def islem_cikar(sade: str):
    """Normalize metinden islem tipini cikarir (belirsizse None)."""
    if _icerir(sade, ("grup ici", "sirketler arasi", "intercompany", "virman")):
        return TRANSFER
    if _icerir(sade, ("ters kayit", "kaydi sil", "kaydi iptal", "geri al", "iptal et")):
        return REVERSAL
    if _icerir(sade, ("duzeltme kaydi", "kaydi duzelt", "yanlis kayit")):
        return CORRECTION
    if _icerir(sade, ("alacak sil", "alacagi sil", "alacagin sil", "alacak mahsup", "alacak duzelt")):
        return RECEIVABLE_ADJUSTMENT
    if _icerir(sade, ("tahsilat", "tahsil et", "tahsil edil", "odeme aldik", "odeme aldim", "odeme geldi",
                      "para geldi", "hesabimiza gecti", "hesaba gecti")):
        return COLLECTION
    if _icerir(sade, ("hakedis", "gelir tahakkuk", "alacak yaz", "alacak kayd", "alacak ekle")):
        return RECEIVABLE
    if re.search(r"\b(odeme|odendi|odedik|odedim|odenmis|odenmistir|odeme yap|yatirildi|gonderildi|havale)\w*", sade):
        return PAYMENT
    if _icerir(sade, ("gider", "masraf", "harcama", "fatura", "maliyet")):
        return EXPENSE
    if re.search(r"\bborc\b", sade):
        return PAYABLE
    if re.search(r"\bgelir\b|\bkazanc\b", sade):
        return INCOME
    return None


def islem_turu_bul(islem_tipi, islem_turleri, ortak=False):
    """Islem tipine karsilik gelen, calisma kitabinda GERCEKTEN tanimli Islem_Turu'nu bulur."""
    adaylar = ([ORTAK_ODEME] if ortak and islem_tipi in (PAYMENT, COUNTERPARTY_PAYMENT) else []) \
        + ISLEM_TURU_ADAYLARI.get(islem_tipi, [])
    mevcut = {norm(t): t for t in islem_turleri}
    for aday in adaylar:
        if norm(aday) in mevcut:
            return mevcut[norm(aday)]
    return None


def nakit_gerekli_mi(islem_turu) -> bool:
    sade = norm(islem_turu)
    return any(a in sade for a in NAKIT_ANAHTARLARI)


# ---------------------------------------------------------------------------
# Sorgu / kayit ayrimi
# ---------------------------------------------------------------------------

def sorgu_mu(sade: str, ham: str, tutar_var: bool) -> bool:
    if "?" in ham:
        return True
    # "Göktürk okuluna ait bütün Devrim ödemelerini yaz" gibi cümleler
    # okuma/listedir; "yaz" kelimesi burada yeni Excel kaydı anlamına gelmez.
    # Tutar varsa ise "10 bin TL ödeme yaz" kayıt talimatı olmaya devam eder.
    if not tutar_var and any(n in sade for n in FINANS_NESNELERI):
        liste_baglam = (
            re.search(r"\b(butun|tum|tüm|tam|liste|kalem kalem|dokum|ayrinti)\b", sade)
            or re.search(r"\b\w+(?:leri|lari|larini|lerini|ler|lar)\b", sade)
        )
        if liste_baglam and any(re.search(rf"\b{fiil}\w*\b", sade) for fiil in LISTE_SORGU_FIILLERI):
            return True
    if _icerir(sade, SORGU_IPUCLARI):
        return not (tutar_var and re.search(r"\b(ekle|kaydet|yaz|isle|gir)\b", sade))
    # "yaptik mi", "edildi mi" gibi soru eki
    if re.search(r"\b(?:yapildi|edildi|odendi|odedik|yaptik|alindi|girildi|islendi|kaydedildi)\s+m[iu]\b", sade):
        return True
    return False


def kayit_mi(sade: str, tutar_var: bool, islem) -> bool:
    komut = bool(re.search(r"\b(ekle|kaydet|yaz|gir|isle|olustur)\w*", sade))
    pasif = bool(re.search(r"\b(yapildi|yapilmistir|odendi|odedik|odedim|alindi|girildi|tahsil\w*)\b", sade))
    if islem is not None:
        return tutar_var or komut or pasif
    return tutar_var and komut


def karmasik_mi(sade: str, ham: str, tutarlar) -> str:
    farkli = {(t["tutar"], t["pb"]) for t in tutarlar}
    if len(farkli) >= 2:
        return "birden fazla tutar"
    if "\n" in ham.strip() or len(ham) > 320:
        return "uzun/çok satırlı talimat"
    if _icerir(sade, GELECEK_ANAHTARLARI):
        return "ileri tarihli/vadeli ifade"
    if _icerir(f" {sade} ", KURALLI_ANAHTARLAR):
        return "çek/kredi/finansman kuralı"
    return ""


# ---------------------------------------------------------------------------
# Ana cozumleme
# ---------------------------------------------------------------------------

_BILINMEYEN_KISI = re.compile(
    r"(?<![\w'’])([A-ZÇĞİÖŞÜ][a-zçğıöşüâîû]{2,}(?:\s+[A-ZÇĞİÖŞÜ][a-zçğıöşüâîû]{2,}){0,2})['’]"
    r"(?:e|a|ye|ya|ten|tan|den|dan|nin|nın|in|ın)\b")


def _aday_ekle(sonuc: Cozum, alan: str, niyet: Niyet):
    if sonuc.durum == TEKIL:
        return sonuc.secilen
    if sonuc.durum == BELIRSIZ:
        niyet.belirsiz[alan] = sonuc.adaylar
    elif sonuc.durum == ONERI:
        niyet.oneri[alan] = sonuc.adaylar
    return None


def analiz_et(metin: str, katalog: Katalog, bugun: date | None = None, zorla: bool = False) -> Niyet:
    bugun = bugun or date.today()
    n = Niyet(metin=str(metin or "").strip())
    ham = n.metin
    sade = norm(ham)
    tutarlar = tutar_cikar(ham)
    n.islem = islem_cikar(sade)

    if not zorla and sorgu_mu(sade, ham, bool(tutarlar)):
        n.tur = "SORGU"
        return n
    if n.islem in (TRANSFER, REVERSAL, CORRECTION):
        n.tur = "KAYIT"
        n.desteklenmeyen = {
            TRANSFER: "Grup içi transferler 'Grup içi transfer' ekranından (kaynak ve hedef faaliyet fişleriyle birlikte) yapılır.",
            REVERSAL: "Kayıt iptali/ters kayıt sohbetten yapılamaz; yetkili kullanıcı belge ekranından şifreyle iptal eder.",
            CORRECTION: "Düzeltme kaydı sohbetten yapılamaz; açık taslağı 'Düzelt:' ile değiştirebilir veya iptal edebilirsiniz.",
        }[n.islem]
        return n
    if not zorla and not kayit_mi(sade, bool(tutarlar), n.islem):
        return n            # ne tutar ne islem: selamlama/sohbet; kayit baslatilmaz
    n.tur = "KAYIT"
    n.karmasik_nedeni = "" if zorla else karmasik_mi(sade, ham, tutarlar)
    n.karmasik = bool(n.karmasik_nedeni)
    n.coklu_tutar = len({(t["tutar"], t["pb"]) for t in tutarlar}) >= 2
    if n.karmasik:
        return n

    if tutarlar:
        n.tutar, n.pb = tutarlar[0]["tutar"], tutarlar[0]["pb"]
    tarih, _, hata = tarih_cikar(ham, bugun)
    if tarih:
        n.tarih, n.tarih_kaynagi = tarih, "metin"
    elif hata:
        n.tarih_hatasi = hata
    elif not zorla:
        n.tarih, n.tarih_kaynagi = bugun, "varsayilan_bugun"
        n.notlar.append("Tarih belirtilmediği için bugün varsayıldı.")
    n.ortak_odemesi = bool(re.search(r"\bortak\b", sade))

    # --- varliklar: faaliyet -> proje -> is kalemi -> cari -> hesap (token tuketimi ile) ---
    tuketilen: frozenset = frozenset()

    c = coz(ham, katalog.faaliyetler, tuketilen, bulanik=True)
    n.faaliyet = _aday_ekle(c, "faaliyet", n)
    if c.durum == TEKIL:
        tuketilen |= c.kullanilan

    proje_adaylari = katalog.projeleri(n.faaliyet.kimlik if n.faaliyet else None)
    c = coz(ham, proje_adaylari, tuketilen)
    secilen_proje = _aday_ekle(c, "proje", n)
    if c.durum == BELIRSIZ and not n.faaliyet:
        ustler = {p.ust for p in c.adaylar}
        # Ayni proje adi birden cok faaliyette: faaliyet belirsiz, proje metinden belli
        n.belirsiz.pop("proje", None)
        n.belirsiz["faaliyet"] = [katalog.faaliyet(u) for u in sorted(ustler) if katalog.faaliyet(u)]
        n.notlar.append("Aynı ada sahip proje birden fazla faaliyette var; faaliyet seçilmeli.")
        tuketilen |= c.kullanilan
    if not secilen_proje and c.durum not in (BELIRSIZ,):
        # "Goktürk projesi": ayirt edici tek kelime + acik "proje" ifadesi -> notla kabul;
        # ifade yoksa yalnizca oneri (kullanici onaylar).
        kismi = kismi_coz(ham, proje_adaylari, tuketilen)
        if len(kismi) == 1:
            if re.search(r"\bproje\w*|\bsantiye\w*", sade):
                secilen_proje = kismi[0]
                n.notlar.append(f"Proje kısmi eşleşmeyle bulundu: {kismi[0].ad}")
                n.oneri.pop("proje", None)
            else:
                n.oneri["proje"] = kismi
        elif len(kismi) > 1:
            n.belirsiz["proje"] = kismi
    if secilen_proje:
        n.proje = secilen_proje
        tuketilen |= c.kullanilan
        if not n.faaliyet:
            n.faaliyet = katalog.faaliyet(secilen_proje.ust)
            if n.faaliyet:
                n.notlar.append(f"Faaliyet projeden çıkarıldı: {secilen_proje.ad} → {n.faaliyet.ad}")

    kalem_adaylari = katalog.kalemleri(n.faaliyet.kimlik if n.faaliyet else None, n.proje.ad if n.proje else None)
    c = coz(ham, kalem_adaylari, tuketilen, bulanik=False)
    n.kalem = _aday_ekle(c, "kalem", n)
    if n.kalem:
        tuketilen |= c.kullanilan
        if not n.proje:
            ust = next((p for p in katalog.projeler if norm(p.ad) == norm(n.kalem.ekle("proje"))
                        and p.ust == n.kalem.ust), None)
            if ust:
                n.proje = ust
                n.notlar.append(f"Proje iş kaleminden çıkarıldı: {n.kalem.ad} → {ust.ad}")
        if not n.faaliyet:
            n.faaliyet = katalog.faaliyet(n.kalem.ust)

    cari_adaylari = katalog.carileri(n.faaliyet.kimlik if n.faaliyet else None)
    c = coz(ham, cari_adaylari, tuketilen)
    n.cari = _aday_ekle(c, "cari", n)
    if n.cari:
        tuketilen |= c.kullanilan
    elif c.durum == YOK and kismi_adaylar(ham, cari_adaylari, tuketilen):
        ortak = kismi_adaylar(ham, cari_adaylari, tuketilen)
        if len(ortak) > 1:
            n.belirsiz["cari"] = ortak
        else:
            n.oneri["cari"] = ortak
    elif c.durum != BELIRSIZ and c.durum != ONERI:
        # Cozumlenmis varliklara (faaliyet/proje/is kalemi) ait kelimeler cikarildiktan sonra kalan metinde
        # "Mehmet'e" gibi tanimsiz bir ozel ad aranir; bulunursa YENI CARI ADAYI olarak (onay icin) isaretlenir.
        varlik_jetonlari = set()
        for x in (n.faaliyet, n.proje, n.kalem):
            if x:
                for ad in (x.ad, *x.takma_adlar):
                    varlik_jetonlari.update(norm(ad).split())
        kalan = " ".join(
            w for w in ham.split()
            if not (norm(w).split() and any(_ek_eslesme(norm(w).split()[0], j) for j in varlik_jetonlari)))
        m = _BILINMEYEN_KISI.search(kalan)
        if m:
            n.yeni_cari_adi = m.group(1).strip()

    hesap_adaylari = katalog.hesaplari(n.faaliyet.kimlik if n.faaliyet else None)
    # Hesap adlari faaliyet adini icerebilir ("Gursar TL Banka"); jeton tuketimi uygulanmaz.
    c = coz(ham, hesap_adaylari, frozenset(), bulanik=False)
    n.hesap = _aday_ekle(c, "hesap", n)

    # --- iş kalemi/cari ayrimi (islem tipini inceltir) ---
    sade_kalem = bool(re.search(r"\bis kalem\w*", sade))
    if n.islem in (PAYMENT, COUNTERPARTY_PAYMENT, WORK_ITEM_PAYMENT):
        if n.kalem or sade_kalem:
            n.islem = WORK_ITEM_PAYMENT
        elif n.cari or n.yeni_cari_adi or n.ortak_odemesi or "cari" in n.belirsiz or "cari" in n.oneri:
            n.islem = COUNTERPARTY_PAYMENT
        # ikisi de yoksa PAYMENT kalir -> sihirbaz sorar
    elif n.islem == EXPENSE and (n.kalem or n.proje):
        n.islem = PROJECT_COST if n.proje else EXPENSE
    return n
