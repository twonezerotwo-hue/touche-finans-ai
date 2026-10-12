"""Yeni faaliyet, proje ve cari açılışını tek Holding master dosyasında yönetir.

Excel yazımı ``excel_deposu`` üzerinden yapılır (tek ANA + tek YEDEK). Faaliyet
kayıt defteri (JSON) ANA yerleştirildikten sonra yazılır; o adım başarısız olursa
ANA otomatik olarak YEDEK'ten geri alınır. Ayrı bir kurulum yedek klasörü yoktur.
"""
import json
import math
import os
import uuid

import config
from config import FIRMALAR, FAALIYET_REGISTRY, master_firma_tanimi
from excel_deposu import guvenli_degistir
from faaliyet_sema import anahtar
from guvenlik import audit_yaz
from kayit_kilidi import kilitli


def metin_kontrol(deger):
    deger = str(deger or '').strip()
    if not 2 <= len(deger) <= 100 or deger[0] in '=+-@' or any(ord(c) < 32 for c in deger):
        raise ValueError('Ad 2–100 karakter olmalı; formül veya kontrol karakteri içeremez.')
    return deger


def _bos_mu(deger):
    return deger is None or (isinstance(deger, str) and not deger.strip())


def _bos(ws, sutunlar, ilk, son):
    """Verilen sütunların HEPSİ boş olan ilk satırı döndürür (kısmen dolu satıra yazılmaz)."""
    if isinstance(sutunlar, str):
        sutunlar = (sutunlar,)
    for r in range(ilk, son + 1):
        if all(_bos_mu(ws[f'{c}{r}'].value) and ws[f'{c}{r}'].data_type != 'f' for c in sutunlar):
            return r
    raise ValueError(f'{ws.title}: {sutunlar[0]}{ilk}:{sutunlar[0]}{son} kapasitesi dolu; mevcut kayıtlar korunuyor.')


def _registry_oku():
    if not FAALIYET_REGISTRY.exists():
        return {'surum': 1, 'faaliyetler': {}}
    data = json.loads(FAALIYET_REGISTRY.read_text(encoding='utf-8'))
    if data.get('surum') != 1:
        raise ValueError('Registry sürümü desteklenmiyor.')
    return data


def _registry_yaz(registry):
    """Registry'yi atomik yazar; önceki sürüm TEK '.bak' dosyasında tutulur."""
    hedef = FAALIYET_REGISTRY
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = hedef.with_name('.' + uuid.uuid4().hex + hedef.suffix)
    try:
        gecici.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding='utf-8')
        json.loads(gecici.read_text(encoding='utf-8'))
        if hedef.exists():
            hedef.with_suffix(hedef.suffix + '.bak').write_bytes(hedef.read_bytes())
        os.replace(gecici, hedef)
    finally:
        gecici.unlink(missing_ok=True)


def _master_sayfalari():
    return [config.FIRMALAR['touche_holding']['ana_ledger']['sayfa'], '99_Ayarlar']


@kilitli
def faaliyet_olustur(ad, proje, hesap, pb='TL', holding_payi=100, dis_ortak='', aktor='sistem'):
    ad, proje, hesap = map(metin_kontrol, (ad, proje, hesap))
    key = anahtar(ad)
    if not key:
        raise ValueError('Faaliyet adına harf veya rakam ekleyin.')
    oran = float(str(holding_payi).replace(',', '.')) / 100
    if not math.isfinite(oran) or not 0 < oran <= 1:
        raise ValueError('Holding payı 0’dan büyük, en fazla 100 olmalı.')
    if oran < 1:
        dis_ortak = metin_kontrol(dis_ortak)
    if pb not in ('TL', 'USD', 'EUR'):
        raise ValueError('Para birimi TL, USD veya EUR olmalı.')
    registry = _registry_oku()
    if key in FIRMALAR or key in registry['faaliyetler'] or any(anahtar(v['ad']) == key for v in FIRMALAR.values()):
        raise ValueError('Bu faaliyet zaten kayıtlı.')
    kayit = {'ad': ad, 'faaliyet': ad, 'excel_mode': 'master', 'pb': pb,
             'varsayilan_hesap': hesap, 'holding_payi': oran, 'dis_ortak': dis_ortak}

    def degistir(wb):
        master = wb['99_Ayarlar']
        if any(anahtar(master[f'A{r}'].value or '') == key for r in range(13, 33)):
            raise ValueError('Holding’de aynı faaliyet zaten var; önce mevcut kayıt eşleştirilmeli.')
        r = _bos(master, ('A', 'B', 'C', 'D', 'E'), 13, 32)
        for col, val in {'A': ad, 'B': oran, 'C': dis_ortak, 'D': 1 - oran, 'E': 'Aktif'}.items():
            master[f'{col}{r}'] = val
        r = _bos(master, ('H', 'I'), 13, 112)
        master[f'H{r}'], master[f'I{r}'] = ad, proje
        r = _bos(master, ('K', 'L', 'M', 'N', 'O'), 13, 62)
        for col, val in {'K': 'HSP-' + uuid.uuid4().hex[:10], 'L': ad, 'M': hesap, 'N': pb, 'O': 0}.items():
            master[f'{col}{r}'] = val
        return {'basari': True}

    def ek_adim(_sonuc):
        yeni = _registry_oku()
        yeni['faaliyetler'][key] = kayit
        _registry_yaz(yeni)

    guvenli_degistir(degistir, aktor=aktor, kaynak='faaliyet_yonetimi.faaliyet_olustur',
                     beklenen_sayfalar=_master_sayfalari(), ek_adim=ek_adim,
                     audit_ek=lambda s: {'faaliyet': key, 'proje': proje, 'hesap': hesap, 'holding_payi': oran})
    FIRMALAR[key] = master_firma_tanimi(kayit)
    audit_yaz('faaliyet_olusturuldu', faaliyet=key, excel_mode='master', holding_payi=oran)
    return key


@kilitli
def proje_olustur(firma_key, proje, aktor='sistem'):
    proje = metin_kontrol(proje)
    firma = FIRMALAR[firma_key]
    if firma_key == 'touche_holding':
        raise ValueError('Önce projenin bağlı olduğu faaliyeti /firma ile seçin.')
    from master_data import master_data_oku
    md = master_data_oku(firma_key)
    if any(anahtar(p) == anahtar(proje) for p in md['projeler']):
        raise ValueError('Bu proje zaten kayıtlı.')
    # Faaliyet adı master veriden gelir (Excel'deki yazım); sabit ad kullanılmaz.
    faaliyet = (md['faaliyetler'] or [firma.get('faaliyet') or firma['ad']])[0]

    def degistir(wb):
        ws = wb['99_Ayarlar']
        r = _bos(ws, ('H', 'I'), 13, 112)
        ws[f'H{r}'], ws[f'I{r}'] = faaliyet, proje
        return {'basari': True}

    guvenli_degistir(degistir, aktor=aktor, kaynak='faaliyet_yonetimi.proje_olustur',
                     beklenen_sayfalar=_master_sayfalari(),
                     audit_ek=lambda s: {'faaliyet': firma_key, 'proje': proje})
    audit_yaz('proje_olusturuldu', faaliyet=firma_key, proje=proje)


@kilitli
def cari_olustur(firma_key, cari, cari_tipi, aktor='sistem'):
    cari, cari_tipi = metin_kontrol(cari), metin_kontrol(cari_tipi)
    firma = FIRMALAR[firma_key]
    if firma_key == 'touche_holding':
        raise ValueError('Cari için önce bağlı faaliyeti seçin.')
    from master_data import master_data_oku
    md = master_data_oku(firma_key)
    if cari_tipi not in md['cari_tipler']:
        raise ValueError('Cari tipi tanımsız: ' + ', '.join(md['cari_tipler']))
    if any(anahtar(c) == anahtar(cari) for c in md['cariler']):
        raise ValueError('Cari zaten kayıtlı.')

    def degistir(wb):
        ws = wb['99_Ayarlar']
        r = _bos(ws, ('AG', 'AH', 'AI'), 13, 112)
        ws[f'AG{r}'], ws[f'AH{r}'], ws[f'AI{r}'] = (md['faaliyetler'] or [firma.get('faaliyet') or firma['ad']])[0], cari_tipi, cari
        return {'basari': True}

    guvenli_degistir(degistir, aktor=aktor, kaynak='faaliyet_yonetimi.cari_olustur',
                     beklenen_sayfalar=_master_sayfalari(),
                     audit_ek=lambda s: {'faaliyet': firma_key, 'cari': cari, 'cari_tipi': cari_tipi})
    audit_yaz('cari_olusturuldu', faaliyet=firma_key, cari=cari, cari_tipi=cari_tipi)
