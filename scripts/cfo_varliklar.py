# -*- coding: utf-8 -*-
"""CFO asistani icin deterministik varlik kayit defteri (faaliyet, proje, cari, hesap, is kalemi).

Yapay zeka varlik UYDURAMAZ: burada yalnizca Smart Excel'deki gercek tanimlar bulunur.
Turkce eklere (okuluna, okulunda, Devrim'e, Gursar'in ...) dayanikli, kural tabanli eslesme
yapilir; iki farkli varlik asla sessizce birlestirilmez (``BELIRSIZ`` sonucu doner).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import config
from faaliyet_sema import anahtar as _faaliyet_anahtar
from master_data import master_data_oku

TEKIL, YOK, BELIRSIZ, ONERI = "TEKIL", "YOK", "BELIRSIZ", "ONERI"

# Bir kelimenin yaklasik eslesmesi (yazim hatasi) icin esik; sonuc yalniz ONERI olur.
_BULANIK_ESIK = 0.86
_MAKS_EK_UZUNLUGU = 6          # "okulunda" -> "okulu" + 3 harf; "devrimlerin" gibi uzun ekler
_MIN_JETON = 2


def norm(metin) -> str:
    """Kucuk harf, aksansiz, tek bosluk: 'Göktürk Okulu' -> 'gokturk okulu'."""
    s = unicodedata.normalize("NFKD", str(metin or "").casefold().replace("ı", "i").replace("İ", "i"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def jetonlar(metin) -> list[str]:
    return norm(metin).split()


@dataclass(frozen=True)
class Varlik:
    tur: str                         # faaliyet | proje | cari | hesap | kalem
    ad: str                          # Excel'deki yazim (kanonik)
    kimlik: str                      # faaliyet: FIRMALAR anahtari; digerleri: normalize ad
    ust: str | None = None           # ust faaliyet anahtari (proje/cari/hesap/kalem)
    ek: tuple = field(default_factory=tuple)      # (anahtar, deger) cifti; hashlenebilir
    takma_adlar: tuple = field(default_factory=tuple)

    def ekle(self, anahtar, varsayilan=None):
        return dict(self.ek).get(anahtar, varsayilan)

    def gorunen(self):
        """Kullaniciya gosterilen ad: faaliyette sahibin kullandigi ad (or. Gursar Mimarlik)."""
        return self.ekle("firma_adi") or self.ad


@dataclass
class Cozum:
    durum: str                       # TEKIL | YOK | BELIRSIZ | ONERI
    secilen: Varlik | None = None
    adaylar: list = field(default_factory=list)
    kullanilan: frozenset = frozenset()          # metinde tuketilen jeton konumlari


def _ek_eslesme(metin_jetonu: str, ad_jetonu: str) -> bool:
    """'okuluna' ~ 'okulu': ad jetonu ile baslar ve fark kisa bir ektir."""
    if metin_jetonu == ad_jetonu:
        return True
    if len(ad_jetonu) < 3:
        return False
    return metin_jetonu.startswith(ad_jetonu) and len(metin_jetonu) - len(ad_jetonu) <= _MAKS_EK_UZUNLUGU


def _alias_eslesmesi(metin_jetonlari: list[str], alias: str, yasak: frozenset = frozenset()):
    """Alias'in TUM jetonlari metinde (herhangi sirada, tuketilmemis konumlarda) varsa konumlari doner."""
    ad_jetonlari = alias.split()
    if not ad_jetonlari:
        return None
    konumlar: list[int] = []
    for aj in ad_jetonlari:
        bulundu = None
        for i, mj in enumerate(metin_jetonlari):
            if i in yasak or i in konumlar:
                continue
            if _ek_eslesme(mj, aj):
                bulundu = i
                break
        if bulundu is None:
            return None
        konumlar.append(bulundu)
    return frozenset(konumlar)


def _bulanik_eslesme(metin_jetonlari: list[str], alias: str, yasak: frozenset = frozenset()):
    ad_jetonlari = alias.split()
    if not ad_jetonlari:
        return None
    konumlar: list[int] = []
    for aj in ad_jetonlari:
        en_oran, en_konum = 0.0, None
        for i, mj in enumerate(metin_jetonlari):
            if i in yasak or i in konumlar or len(mj) < 4 or len(aj) < 4:
                continue
            oran = SequenceMatcher(None, mj, aj).ratio()
            if oran > en_oran:
                en_oran, en_konum = oran, i
        if en_konum is None or en_oran < _BULANIK_ESIK:
            return None
        konumlar.append(en_konum)
    return frozenset(konumlar)


def coz(metin, adaylar: list[Varlik], yasak: frozenset = frozenset(), bulanik: bool = True) -> Cozum:
    """Metindeki ifadeyi aday varliklara karsi cozer.

    Tam eslesmeler arasinda en cok jeton kapsayan (en spesifik) kazanir; esitlikte farkli
    varliklar varsa BELIRSIZ doner. Tam eslesme yoksa yazim hatasi benzerligi yalniz ONERI
    uretir (otomatik secilmez).
    """
    mj = jetonlar(metin)
    tam: list[tuple[int, Varlik, frozenset]] = []
    for v in adaylar:
        en_iyi = None
        for alias in {norm(v.ad), *(norm(a) for a in v.takma_adlar)}:
            konum = _alias_eslesmesi(mj, alias, yasak)
            if konum and (en_iyi is None or len(konum) > len(en_iyi)):
                en_iyi = konum
        if en_iyi:
            tam.append((len(en_iyi), v, en_iyi))
    if tam:
        en_cok = max(t[0] for t in tam)
        kazananlar = [t for t in tam if t[0] == en_cok]
        ayri = {t[1].kimlik + "|" + str(t[1].ust) for t in kazananlar}
        if len(ayri) == 1:
            return Cozum(TEKIL, kazananlar[0][1], [kazananlar[0][1]], kazananlar[0][2])
        return Cozum(BELIRSIZ, None, [t[1] for t in kazananlar],
                     frozenset().union(*(t[2] for t in kazananlar)))
    if bulanik:
        yakin = []
        for v in adaylar:
            for alias in {norm(v.ad), *(norm(a) for a in v.takma_adlar)}:
                konum = _bulanik_eslesme(mj, alias, yasak)
                if konum:
                    yakin.append((v, konum))
                    break
        if yakin:
            return Cozum(ONERI, None, [y[0] for y in yakin], frozenset().union(*(y[1] for y in yakin)))
    return Cozum(YOK)


def kismi_coz(metin, adaylar: list[Varlik], yasak: frozenset = frozenset()) -> list[Varlik]:
    """Adin yalniz AYIRT EDICI bir kelimesi gecen adaylari dondurur ('Goktürk projesi' -> Göktürk Okulu).

    Bir aday ancak metindeki bir jetonla eslesen kelimesi, kapsamdaki BASKA hicbir adayda
    bulunmuyorsa (ayirt edici) listelenir; boylece yanlis varlik secilmez.
    """
    mj = jetonlar(metin)
    kelime_sahipleri: dict[str, set] = {}
    for v in adaylar:
        for kelime in norm(v.ad).split():
            kelime_sahipleri.setdefault(kelime, set()).add(v.kimlik + "|" + str(v.ust))
    sonuc = []
    for v in adaylar:
        for kelime in norm(v.ad).split():
            if len(kelime) < 4 or len(kelime_sahipleri.get(kelime, ())) != 1:
                continue
            if any(i not in yasak and _ek_eslesme(j, kelime) for i, j in enumerate(mj)):
                sonuc.append(v)
                break
    return sonuc


def kismi_adaylar(metin, adaylar: list[Varlik], yasak: frozenset = frozenset()) -> list[Varlik]:
    """Adin herhangi bir (>=4 harf) kelimesi metinde gecen TUM adaylar (ayirt edici olmasi gerekmez).

    'Devrim'e' + {Devrim Yildiz, Devrim Kaya} -> iki aday: sistem karar vermez, kullaniciya sorar.
    """
    mj = jetonlar(metin)
    sonuc = []
    for v in adaylar:
        for kelime in norm(v.ad).split():
            if len(kelime) >= 4 and any(i not in yasak and _ek_eslesme(j, kelime) for i, j in enumerate(mj)):
                sonuc.append(v)
                break
    return sonuc


@dataclass
class Katalog:
    faaliyetler: list = field(default_factory=list)
    projeler: list = field(default_factory=list)
    hesaplar: list = field(default_factory=list)
    cariler: list = field(default_factory=list)
    kalemler: list = field(default_factory=list)
    islem_turleri: list = field(default_factory=list)
    kurlar: dict = field(default_factory=dict)
    md: dict = field(default_factory=dict)

    # --- sorgular ---
    def faaliyet(self, kimlik):
        return next((f for f in self.faaliyetler if f.kimlik == kimlik), None)

    def projeleri(self, faaliyet_kimlik=None):
        return [p for p in self.projeler if faaliyet_kimlik in (None, p.ust)]

    def hesaplari(self, faaliyet_kimlik=None):
        return [h for h in self.hesaplar if faaliyet_kimlik in (None, h.ust)]

    def carileri(self, faaliyet_kimlik=None):
        return [c for c in self.cariler if faaliyet_kimlik in (None, c.ust)]

    def kalemleri(self, faaliyet_kimlik=None, proje_ad=None):
        sonuc = [k for k in self.kalemler if faaliyet_kimlik in (None, k.ust)]
        if proje_ad:
            sonuc = [k for k in sonuc if norm(k.ekle("proje")) == norm(proje_ad)]
        return sonuc


def _faaliyet_takma_adlari(key, firma, tum_firmalar):
    adlar = [firma.get("ad"), firma.get("faaliyet"), key.replace("_", " "), *(firma.get("faaliyet_aliaslari") or [])]
    adlar = [a for a in adlar if a]
    ilk_jetonlar: dict[str, list[str]] = {}
    for k, f in tum_firmalar.items():
        jt = jetonlar(f.get("ad"))
        if jt:
            ilk_jetonlar.setdefault(jt[0], []).append(k)
    ilk = jetonlar(firma.get("ad"))
    if ilk and len(ilk[0]) >= 4 and ilk_jetonlar.get(ilk[0]) == [key]:
        adlar.append(ilk[0])
    return tuple(dict.fromkeys(adlar))


def _excel_faaliyet_adi(firma, key, md_faaliyetleri):
    """FIRMALAR kaydina karsilik gelen, Excel 99_Ayarlar'daki faaliyet adini bulur."""
    adaylar = {_faaliyet_anahtar(a) for a in
               [firma.get("ad"), firma.get("faaliyet"), key, *(firma.get("faaliyet_aliaslari") or [])] if a}
    return next((f for f in md_faaliyetleri if _faaliyet_anahtar(f) in adaylar), None)


def katalog_yukle(kalem_oku=True) -> Katalog:
    """Ana Excel'den (onbellekli) tum varliklari yukler."""
    md = master_data_oku("touche_holding")
    firmalar = config.FIRMALAR
    k = Katalog(md=md, islem_turleri=list(md.get("islem_turleri", [])), kurlar=dict(md.get("kurlar", {})))
    excel_ad_to_key = {}
    for key, firma in firmalar.items():
        excel_ad = _excel_faaliyet_adi(firma, key, md.get("faaliyetler", []))
        if not excel_ad:
            continue            # Excel'de tanimi olmayan faaliyet asistanda secenek olmaz
        excel_ad_to_key[_faaliyet_anahtar(excel_ad)] = key
        k.faaliyetler.append(Varlik(
            "faaliyet", excel_ad, key, None, (("firma_adi", firma.get("ad")),),
            _faaliyet_takma_adlari(key, firma, firmalar)))

    def ust_key(excel_faaliyet):
        return excel_ad_to_key.get(_faaliyet_anahtar(excel_faaliyet))

    for kayit in md.get("proje_hiyerarsisi", []):
        ust = ust_key(kayit["faaliyet"])
        if ust:
            k.projeler.append(Varlik("proje", kayit["proje"], norm(kayit["proje"]), ust))
    hesap_pb = md.get("hesap_pb", {})
    for kayit in md.get("hesap_hiyerarsisi", []):
        ust = ust_key(kayit["faaliyet"])
        if ust:
            k.hesaplar.append(Varlik("hesap", kayit["hesap"], norm(kayit["hesap"]), ust,
                                     (("pb", hesap_pb.get(kayit["hesap"], "TL")),)))
    for kayit in md.get("cari_hiyerarsisi", []):
        ust = ust_key(kayit["faaliyet"])
        if ust:
            k.cariler.append(Varlik("cari", kayit["cari"], norm(kayit["cari"]), ust,
                                    (("cari_tipi", kayit.get("cari_tipi") or ""),)))
    if kalem_oku:
        try:
            import proje_is_kalemleri

            for x in proje_is_kalemleri.listele("tum")["kalemler"]:
                if norm(x["durum"]) == "iptal" or not x["kalem_id"]:
                    continue
                k.kalemler.append(Varlik(
                    "kalem", x["is_kalemi"], x["kalem_id"], x["faaliyet_key"],
                    (("proje", x["proje"]), ("grup", x["maliyet_grubu"]), ("durum", x["durum"]))))
        except Exception:
            pass        # is kalemi sayfasi yoksa is kalemi secenegi sunulmaz (uydurulmaz)
    return k
