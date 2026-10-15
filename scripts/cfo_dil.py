# -*- coding: utf-8 -*-
"""CFO asistani icin Turkce dil yardimcilari (LLM'siz, deterministik).

Burada yalnizca *dili* cozeriz; hangi kaydin yazilacagina karar vermeyiz:

* yaziyla yazilmis tutar ("yirmi bes bin", "bir bucuk milyon") ve "10 bin 500" -> rakam
* goreli tarih ("gecen cuma", "3 gun once", "ayin 5'inde") ve sorgu donemi ("bu hafta", "son 30 gun", "ilk ceyrek")
* olumsuz / gereklilik / gelecek kipi ("odeme yapmadik", "yapmam lazim", "odeyecegiz"): bunlar KAYIT degildir
* "kasadan", "elden", "bankadan", "havale" gibi hesap ipuclari

Belirsiz bir ifade tahmin edilmez: ``None``/bos doner, cagiran taraf sorar ya da mevcut hatta birakir.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

AYLAR = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7,
         "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}
AY_ADLARI = {1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran", 7: "Temmuz",
             8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"}
_AY_DESENI = "|".join(AYLAR)
GUNLER = {"pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}
_GUN_DESENI = "|".join(GUNLER)
GUN_ADLARI = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]
BIRIM_KELIMELER = ("gun", "ay", "adet", "kez", "saat", "kayit", "hafta", "yil", "kisi", "tane", "metre", "m2")


def _sade(metin) -> str:
    """cfo_varliklar.norm ile ayni kural (bu modul onu import etmeden bagimsiz test edilebilsin diye burada)."""
    s = unicodedata.normalize("NFKD", str(metin or "").casefold().replace("ı", "i").replace("İ", "i"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _haritali_sade(metin):
    """Normalize metin + her karakterin ORIJINAL metindeki konumu (bosluk tekrarlari korunur)."""
    ham = str(metin or "")
    cikti, harita = [], []
    for i, ch in enumerate(ham):
        s = unicodedata.normalize("NFKD", ch.casefold().replace("ı", "i").replace("İ", "i"))
        s = "".join(c for c in s if not unicodedata.combining(c))
        for c in s:
            cikti.append(c if c.isascii() and c.isalnum() else " ")
            harita.append(i)
        if not s:                       # birlestirici isaret vb.: konumu koru
            cikti.append(" ")
            harita.append(i)
    return "".join(cikti), harita


# ---------------------------------------------------------------------------
# Yaziyla yazilmis tutarlar
# ---------------------------------------------------------------------------

_BIRLER = {"sifir": 0, "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9}
_ONLAR = {"on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmis": 60, "yetmis": 70, "seksen": 80,
          "doksan": 90}
_OLCEKLER = {"bin": 1_000, "milyon": 1_000_000, "milyar": 1_000_000_000}
_SAYI_KELIMELERI = set(_BIRLER) | set(_ONLAR) | set(_OLCEKLER) | {"yuz", "bucuk", "yarim"}
_PARA_SONRASI = {"tl", "try", "lira", "liralik", "usd", "dolar", "dolarlik", "eur", "euro", "avro", "usdt"}
_KELIME_RE = re.compile(r"[^\W\d_]+")


def _kelime_degeri(tokenler) -> Decimal:
    toplam, mevcut = Decimal(0), Decimal(0)
    for t in tokenler:
        if t in _BIRLER:
            mevcut += _BIRLER[t]
        elif t in _ONLAR:
            mevcut += _ONLAR[t]
        elif t == "yuz":
            mevcut = (mevcut if mevcut else Decimal(1)) * 100
        elif t in ("bucuk", "yarim"):
            mevcut += Decimal("0.5")
        elif t in _OLCEKLER:
            toplam += (mevcut if mevcut else Decimal(1)) * _OLCEKLER[t]
            mevcut = Decimal(0)
    return toplam + mevcut


def _sayi_metni(d: Decimal) -> str:
    return str(int(d)) if d == d.to_integral_value() else str(d)


def _yazili_sayilari_cevir(metin: str) -> str:
    kelimeler = [(m.start(), m.end(), _sade(m.group(0))) for m in _KELIME_RE.finditer(metin)]
    kosular: list[list[tuple[int, int, str]]] = []
    aktif: list[tuple[int, int, str]] = []
    for k in kelimeler:
        if k[2] in _SAYI_KELIMELERI and (not aktif or not metin[aktif[-1][1]:k[0]].strip()):
            aktif.append(k)
            continue
        if aktif:
            kosular.append(aktif)
        aktif = [k] if k[2] in _SAYI_KELIMELERI else []
    if aktif:
        kosular.append(aktif)
    degisiklikler = []
    for kosu in kosular:
        bas, son = kosu[0][0], kosu[-1][1]
        tokenler = [k[2] for k in kosu]
        onceki = metin[:bas].rstrip()
        if onceki and (onceki[-1].isdigit() or onceki[-1] in ".,"):
            continue                                    # "10 bin" gibi rakamli ifadenin parcasi
        sonraki = _KELIME_RE.match(metin[son:].lstrip())
        para_sonrasi = sonraki is not None and _sade(sonraki.group(0)) in _PARA_SONRASI
        olcek_var = any(t in _OLCEKLER for t in tokenler)
        if not (olcek_var or para_sonrasi):
            continue                                    # "bir ödeme", "on kişi": tutar degil
        if tokenler == ["bir"] or (tokenler in (["bin"], ["yarim"], ["bucuk"]) and not para_sonrasi):
            continue                                    # yalniz "bir" / yalniz "bin": tutar sayilmaz
        deger = _kelime_degeri(tokenler)
        if deger <= 0:
            continue
        degisiklikler.append((bas, son, _sayi_metni(deger)))
    for bas, son, yeni in reversed(degisiklikler):
        metin = metin[:bas] + yeni + metin[son:]
    return metin


def _birim_engeli(metin: str, son: int) -> bool:
    sonraki = _sade(metin[son:son + 14]).split()
    return bool(sonraki) and (sonraki[0] in BIRIM_KELIMELER or sonraki[0] in AYLAR)


_MILYON_BIN_RE = re.compile(r"(?<![\d.,])(\d{1,3})\s*milyon\s+(\d{1,3})\s*bin(?:\s+(\d{1,3}))?(?![\d.,])", re.IGNORECASE)
_BIN_YUZ_RE = re.compile(r"(?<![\d.,])(\d{1,3})\s*bin\s+(\d{1,3})(?![\d.,])", re.IGNORECASE)


def _rakamli_toplamlari_birlestir(metin: str) -> str:
    def milyon_bin(m):
        if m.group(3) and _birim_engeli(metin, m.end()):
            return m.group(0)
        d = int(m.group(1)) * 1_000_000 + int(m.group(2)) * 1_000 + int(m.group(3) or 0)
        return str(d)

    def bin_yuz(m):
        alt = m.group(2)
        sonraki = _sade(metin[m.end():m.end() + 14]).split()
        para = bool(sonraki) and sonraki[0] in _PARA_SONRASI
        if _birim_engeli(metin, m.end()) or (len(alt) < 3 and not para):
            return m.group(0)
        return str(int(m.group(1)) * 1_000 + int(alt))

    return _BIN_YUZ_RE.sub(bin_yuz, _MILYON_BIN_RE.sub(milyon_bin, metin))


def sayilari_cevir(metin) -> str:
    """'on bin lira' -> '10000 lira'; 'yarim milyon TL' -> '500000 TL'; '10 bin 500 TL' -> '10500 TL'."""
    ham = str(metin or "")
    return _rakamli_toplamlari_birlestir(_yazili_sayilari_cevir(ham))


# ---------------------------------------------------------------------------
# Goreli tarihler (kayit tarihi)
# ---------------------------------------------------------------------------

_KUCUK_SAYI = {**_BIRLER, **_ONLAR}


def _kucuk_sayi(token: str):
    if token.isdigit():
        return int(token)
    return _KUCUK_SAYI.get(token)


def ay_ekle(d: date, n: int) -> date:
    """d tarihine n ay ekler/cikarir; gun, hedef ayin son gununu asarsa son gune kisaltilir."""
    toplam = d.year * 12 + (d.month - 1) + n
    yil, ay = divmod(toplam, 12)
    ay += 1
    return date(yil, ay, min(d.day, calendar.monthrange(yil, ay)[1]))


def _gecen_gun(bugun: date, gun_no: int) -> date:
    """Bugunden ONCEKI en yakin gun_no gunu (bugun ayni gunse 7 gun once)."""
    fark = (bugun.weekday() - gun_no) % 7
    return bugun - timedelta(days=fark or 7)


def _hafta_basi(d: date) -> date:
    return d - timedelta(days=d.weekday())


_SAYI_DESENI = r"\d{1,3}|bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|yirmi|otuz"
_DMY_KISA_RE = re.compile(
    r"(?<![\d.,/-])(\d{1,2})[./](\d{1,2})(?![\d.,/])\s*['’]?\s*(?:tarih\w*|(?:de|da|te|ta)\b|g[uü]n[uü]\b|itibariyle)",
    re.IGNORECASE)

# (ad, ASCII regex) -- sirayla denenir; ilk eslesen kazanir. Metin _haritali_sade ciktisinda aranir.
_TARIH_KALIPLARI = [
    ("evvelsi", re.compile(r"\b(?:evvelsi|evvelki|onceki)\s+gun(?:u|ku)?\b")),
    ("dun", re.compile(r"\bdun(?:ku)?\b")),
    ("bugun", re.compile(r"\b(?:bugun(?:ku)?|bu\s+(?:sabah|aksam|oglen)|az\s+once)\b")),
    ("n_once", re.compile(rf"\b({_SAYI_DESENI})\s+(gun|hafta|ay)\s+once\b")),
    ("hafta_gun", re.compile(rf"\b(gecen|bu)\s+hafta\s+({_GUN_DESENI})\b")),
    ("gecen_gun", re.compile(rf"\bgecen\s+({_GUN_DESENI})\b")),
    ("gun_gunu", re.compile(rf"\b({_GUN_DESENI})\s+gunu\b")),
    ("gun_yalin", re.compile(r"\b(pazartesi|carsamba|persembe|cuma|cumartesi)\b")),
    ("gecen_ay_gunu", re.compile(r"\bgecen\s+ay(?:in)?\s+(\d{1,2})\b")),
    ("bu_ay_gunu", re.compile(r"\bbu\s+ay(?:in)?\s+(\d{1,2})\b")),
    ("ayin_gunu", re.compile(r"\bayin\s+(\d{1,2})\b")),
    ("gun_eki", re.compile(r"\b(\d{1,2})\s+(?:inde|inda|unde|unda|sinde|sinda)\b")),
]


def _tarih_hesapla(ad, m, bugun: date):
    """Eslesen kalibin tarihini dondurur; gecersiz tarihte ValueError."""
    if ad == "evvelsi":
        return bugun - timedelta(days=2)
    if ad == "dun":
        return bugun - timedelta(days=1)
    if ad == "bugun":
        return bugun
    if ad == "n_once":
        n = _kucuk_sayi(m.group(1)) or 0
        birim = m.group(2)
        if birim == "gun":
            return bugun - timedelta(days=n)
        if birim == "hafta":
            return bugun - timedelta(days=7 * n)
        return ay_ekle(bugun, -n)
    if ad == "hafta_gun":
        pazartesi = _hafta_basi(bugun) - (timedelta(days=7) if m.group(1) == "gecen" else timedelta(0))
        sonuc = pazartesi + timedelta(days=GUNLER[m.group(2)])
        return sonuc - timedelta(days=7) if sonuc > bugun else sonuc
    if ad in ("gecen_gun", "gun_gunu", "gun_yalin"):
        return _gecen_gun(bugun, GUNLER[m.group(1)])
    gun = int(m.group(1))
    if ad == "gecen_ay_gunu":
        h = ay_ekle(bugun.replace(day=1), -1)
        return date(h.year, h.month, gun)
    if ad == "bu_ay_gunu":
        return date(bugun.year, bugun.month, gun)
    # "ayin 5'i", "5'inde": gecmis odakli; bu ayin o gunu henuz gelmediyse onceki ay
    h = bugun.replace(day=1)
    if gun > bugun.day:
        h = ay_ekle(h, -1)
    return date(h.year, h.month, gun)


def _tum_tarih_eslesmeleri(metin):
    """[(ad, eslesme, (orijinal_bas, orijinal_son))] -- tum kaliplar, konum sirasiyla."""
    sade, harita = _haritali_sade(metin)
    bulunan = []
    for ad, kalip in _TARIH_KALIPLARI:
        for m in kalip.finditer(sade):
            bulunan.append((ad, m, (harita[m.start()], harita[m.end() - 1] + 1)))
    return bulunan


def gorece_tarih(metin, bugun: date | None = None):
    """(tarih, (bas, son), hata) doner; yoksa (None, None, '')."""
    bugun = bugun or date.today()
    ham = str(metin or "")
    for ad, m, span in _tum_tarih_eslesmeleri(ham):
        try:
            return _tarih_hesapla(ad, m, bugun), span, ""
        except ValueError:
            return None, span, f"Geçersiz tarih: {ham[span[0]:span[1]]}"
    kisa = _DMY_KISA_RE.search(ham)
    if kisa:
        g, a = int(kisa.group(1)), int(kisa.group(2))
        try:
            sonuc = date(bugun.year, a, g)
            if sonuc > bugun:
                sonuc = date(bugun.year - 1, a, g)
            return sonuc, kisa.span(), ""
        except ValueError:
            return None, kisa.span(), f"Geçersiz tarih: {kisa.group(0)}"
    return None, None, ""


def tarih_ifadelerini_sil(metin) -> str:
    """Tarih ifadelerini bosluga cevirir (tutar taramasinda tarih sayilari tutar sanilmasin)."""
    ham = str(metin or "")
    aralik = [span for _, _, span in _tum_tarih_eslesmeleri(ham)]
    aralik += [m.span() for m in _DMY_KISA_RE.finditer(ham)]
    for bas, son in sorted(aralik, reverse=True):
        ham = ham[:bas] + " " * (son - bas) + ham[son:]
    return ham


# ---------------------------------------------------------------------------
# Sorgu donemi
# ---------------------------------------------------------------------------

_ARALIK_AY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:\s+({_AY_DESENI}))?(?:\s+(20\d{{2}}))?\s+(?:(?:ile|ila|ve|dan|den|tan|ten)\s+)?"
    rf"(\d{{1,2}})\s+({_AY_DESENI})(?:\s+(20\d{{2}}))?\b")
_ARALIK_SAYISAL_RE = re.compile(
    r"(\d{1,2})[./](\d{1,2})[./](20\d{2})\s*(?:-|–|ile|ila|ve)?\s*(\d{1,2})[./](\d{1,2})[./](20\d{2})")
_CEYREK_KELIME = {"ilk": 1, "birinci": 1, "ikinci": 2, "ucuncu": 3, "dorduncu": 4, "son": 4}


def _ay_sonu(yil, ay) -> date:
    return date(yil, ay, calendar.monthrange(yil, ay)[1])


def _gun_etiketi(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _aralik_donemi(sade: str, ham: str, bugun: date):
    s = _ARALIK_SAYISAL_RE.search(ham)
    if s:
        g1, a1, y1, g2, a2, y2 = map(int, s.groups())
        try:
            bas, bit = date(y1, a1, g1), date(y2, a2, g2)
        except ValueError:
            return None
        return (bas, bit, f"{_gun_etiketi(bas)} – {_gun_etiketi(bit)}") if bas <= bit else None
    m = _ARALIK_AY_RE.search(sade)
    if not m:
        return None
    gun1, ay1, yil1_metni, gun2, ay2, yil2_metni = m.groups()
    yil2 = int(yil2_metni) if yil2_metni else (int(yil1_metni) if yil1_metni else bugun.year)
    yil1 = int(yil1_metni) if yil1_metni else yil2
    ay2_no = AYLAR[ay2]
    ay1_no = AYLAR[ay1] if ay1 else ay2_no
    if not ay1 and int(gun1) > int(gun2):
        return None
    try:
        bas, bit = date(yil1, ay1_no, int(gun1)), date(yil2, ay2_no, int(gun2))
    except ValueError:
        return None
    if bas > bit:
        return None
    return bas, bit, f"{_gun_etiketi(bas)} – {_gun_etiketi(bit)}"


def donem_cozumle(metin, bugun: date | None = None):
    """Sorgudaki donem ifadesini (bas, bit, etiket) yapar; taninmayan ifadede None (cagiran eski cozumleyiciye duser)."""
    bugun = bugun or date.today()
    ham = str(metin or "")
    sade = _sade(re.sub(r"(?<=\d)\s*[-–]\s*(?=\d)", " ile ", ham))
    sade_ham = _sade(ham)
    aralik = _aralik_donemi(sade, ham, bugun)
    if aralik:
        return aralik
    m = re.search(rf"\bson\s+({_SAYI_DESENI})\s+(gun|hafta|ay|yil|sene)\w*\b", sade_ham)
    if m:
        n = _kucuk_sayi(m.group(1))
        if n:
            birim = m.group(2)
            if birim == "gun":
                bas, etiket = bugun - timedelta(days=n - 1), f"Son {n} gün"
            elif birim == "hafta":
                bas, etiket = bugun - timedelta(days=7 * n - 1), f"Son {n} hafta"
            elif birim == "ay":
                bas, etiket = ay_ekle(bugun, -n) + timedelta(days=1), f"Son {n} ay"
            else:
                bas, etiket = ay_ekle(bugun, -12 * n) + timedelta(days=1), f"Son {n} yıl"
            return bas, bugun, etiket
    if re.search(r"\byil\s+basindan\b|\byilbasindan\b", sade_ham):
        return date(bugun.year, 1, 1), bugun, f"Yıl başından bugüne ({bugun.year})"
    if re.search(r"\bay\s+basindan\b", sade_ham):
        return bugun.replace(day=1), bugun, f"Ay başından bugüne ({AY_ADLARI[bugun.month]} {bugun.year})"
    if re.search(r"\b(?:bugun|bugunku)\b", sade_ham):
        return bugun, bugun, f"Bugün ({_gun_etiketi(bugun)})"
    if re.search(r"\bdun(?:ku)?\b", sade_ham):
        d = bugun - timedelta(days=1)
        return d, d, f"Dün ({_gun_etiketi(d)})"
    if re.search(r"\bgecen\s+hafta\w*\b", sade_ham):
        bas = _hafta_basi(bugun) - timedelta(days=7)
        return bas, bas + timedelta(days=6), f"Geçen hafta ({_gun_etiketi(bas)} – {_gun_etiketi(bas + timedelta(days=6))})"
    if re.search(r"\bbu\s+hafta\w*\b", sade_ham):
        bas = _hafta_basi(bugun)
        return bas, bas + timedelta(days=6), f"Bu hafta ({_gun_etiketi(bas)} – {_gun_etiketi(bas + timedelta(days=6))})"
    ceyrek = _ceyrek_donemi(sade_ham, bugun)
    if ceyrek:
        return ceyrek
    if re.search(r"\bilk\s+(?:6|alti)\s+ay\b|\bilk\s+yari\w*\b|\bbirinci\s+yari\w*\b", sade_ham):
        yil = _acik_yil(sade_ham, bugun)
        return date(yil, 1, 1), date(yil, 6, 30), f"{yil} ilk yarı (Ocak–Haziran)"
    if re.search(r"\bgecen\s+(?:yil|sene)\b", sade_ham):
        return date(bugun.year - 1, 1, 1), date(bugun.year - 1, 12, 31), str(bugun.year - 1)
    if re.search(r"\bbu\s+(?:yil|sene)\b", sade_ham) and not re.search(rf"\b(?:{_AY_DESENI})\b", sade_ham):
        return date(bugun.year, 1, 1), date(bugun.year, 12, 31), str(bugun.year)
    # "Ekimde", "Eylulden": ay adi ekli (apostrofsuz). Ekli degil ("Ekim", "Eylul'de") eski cozumleyiciye birakilir.
    ek = re.search(rf"\b({_AY_DESENI})(?:de|da|te|ta|den|dan|ten|tan|in|un|nin|nun|ki)\b", sade_ham)
    if ek:
        ay = AYLAR[ek.group(1)]
        yil = _acik_yil(sade_ham, bugun)
        if not re.search(r"\b20\d{2}\b", sade_ham) and ay > bugun.month:
            yil -= 1                              # tamamlanmis hareket sorusu: gelecek ay = gecen yilin o ayi
        return date(yil, ay, 1), _ay_sonu(yil, ay), f"{AY_ADLARI[ay]} {yil}"
    return None


def _acik_yil(sade: str, bugun: date) -> int:
    y = re.search(r"\b(20\d{2})\b", sade)
    return int(y.group(1)) if y else bugun.year


def _ceyrek_donemi(sade: str, bugun: date):
    yil = _acik_yil(sade, bugun)
    q = None
    m = re.search(r"\b(?:q([1-4])|([1-4])\s*(?:inci|nci|uncu)?\s+ceyrek\w*)\b", sade)
    if m:
        q = int(m.group(1) or m.group(2))
    else:
        m = re.search(r"\b(ilk|birinci|ikinci|ucuncu|dorduncu)\s+ceyrek\w*\b", sade)
        if m:
            q = _CEYREK_KELIME[m.group(1)]
        elif re.search(r"\bbu\s+ceyrek\w*\b", sade):
            q = (bugun.month - 1) // 3 + 1
        elif re.search(r"\bgecen\s+ceyrek\w*\b", sade):
            q = (bugun.month - 1) // 3
            if q == 0:
                q, yil = 4, bugun.year - 1
    if not q:
        return None
    bas = date(yil, 3 * q - 2, 1)
    bit = _ay_sonu(yil, 3 * q)
    return bas, bit, f"{yil} {q}. çeyrek ({AY_ADLARI[3 * q - 2]}–{AY_ADLARI[3 * q]})"


# ---------------------------------------------------------------------------
# Kip: olumsuz / gereklilik / gelecek -- bunlar gerceklesmis bir hareketi ANLATMAZ
# ---------------------------------------------------------------------------

_FIIL_KOKU = r"(?:ode|yap|ver|gonder|yatir|aktar|tahsil\s+(?:et|ed)|isle|kaydet|al|alin|gir|gel|gec|yat|et|ed|ol)"
_OLUMSUZ_SON = (r"(?:m[ae](?:di\w*|dik\w*|dim|yecek\w*|yecegi\w*|yacak\w*|yacag\w*|mis\w*|yiz)|m[iu]yor\w*)")
_OLUMSUZ_RE = re.compile(rf"\b{_FIIL_KOKU}(?:y?[ae]|il|in|n)?{_OLUMSUZ_SON}\b")
_GEREKLILIK_RE = re.compile(
    r"\b(?:lazim|gerek(?:iyor|li|ir|iyordu)?|zorunda|unut\w+)\b"
    r"|\b(?:ode|yap|ver|gonder|yatir|aktar)\w*?(?:meli|mali)\w*\b"
    r"|\b(?:ode|yap|ver|gonder|yatir|aktar)(?:yelim|elim|alim|yeyim|eyim|ayim|sek|sak)\b"
    r"|\b(?:ode|yap|ver|gonder|yatir|aktar)(?:y?abil|y?ebil)\w*\b"
    # koşul kipi: "odemek gerekirse", "odese", "yapsa", "olursa" (henuz gerceklesmemis)
    r"|\b(?:ode|yap|ver|gonder|yatir|aktar|gerek|ol)\w*?(?:rse|rsa|se|sa)\b"
    r"|\b(?:istiyor\w*|istedi\w*|talep|rica|planl\w*|dusun\w*|niyet\w*|bekl\w*)\b")
# "alacak" ismi (receivable) fiil sayilmaz: gelecek kipi yalniz para hareketi fiillerinde aranir
_GELECEK_RE = re.compile(
    r"\b(?:ode|yap|ver|gonder|yatir|aktar|tahsil\s+et|alin)\w*?(?:ecek|acak|ecegi|acagi|eceg|acag)\w*\b")


def olumsuz_mu(sade: str) -> bool:
    """'odeme yapmadik', 'yapilmadi', 'odenmedi', 'odeyemedik', 'odemeyecegiz', 'tahsilat gelmedi'."""
    return bool(_OLUMSUZ_RE.search(sade))


def gereklilik_mi(sade: str) -> bool:
    """'yapmam lazim', 'yapilmasi gerekiyor', 'odeyelim', 'unuttum', 'istiyor': gerceklesmemis/istek."""
    return bool(_GEREKLILIK_RE.search(sade))


def gelecek_mi(sade: str) -> bool:
    return bool(_GELECEK_RE.search(sade))


# ---------------------------------------------------------------------------
# Hesap ipuclari
# ---------------------------------------------------------------------------

_KASA_ONEKLERI = ("kasa", "elden", "nakit", "nakden", "pesin")
_BANKA_ONEKLERI = ("banka", "havale", "eft", "fast", "swift")


def hesap_ipucu(jetonlar, yasak=frozenset()) -> str:
    """'KASA' (kasadan/elden/nakit), 'BANKA' (bankadan/havale/EFT) ya da '' (yok veya celisik)."""
    kasa = any(i not in yasak and j.startswith(_KASA_ONEKLERI) for i, j in enumerate(jetonlar))
    banka = any(i not in yasak and j.startswith(_BANKA_ONEKLERI) for i, j in enumerate(jetonlar))
    if kasa == banka:
        return ""
    return "KASA" if kasa else "BANKA"
