"""Standart faaliyet dosyasinin surumlu sutun sozlesmesi."""
import re
import unicodedata

SUTUNLAR = dict(zip(
    ['Kayit_ID','Tarih','Saat','Faaliyet','Proje','Cari_Tipi','Cari','Islem_Turu',
     'Hesap_Kasa','Tutar','PB','Kur','Tutar_TL','Gelir_TL','Gider_TL','Nakit_TL',
     'Durum','Kaynak_Belge_ID','Aciklama','AI_Guven','Onay_Durumu','Kayit_Kaynak',
     'Belge_Hash','Fingerprint'],
    list('ABCDEFGHIJKLMNOPQRSTUVWX')))

def anahtar(ad):
    text = unicodedata.normalize('NFKD', str(ad).casefold().replace('ı', 'i'))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_')[:36]

def firma_tanimi(kayit):
    return {**kayit, 'adapter': 'standart_v1',
            'ana_ledger': {'sayfa': 'İşlem Kayıtları', 'baslik_satiri': 5,
                           'ilk_veri_satiri': 6, 'sutunlar': dict(SUTUNLAR)}}
