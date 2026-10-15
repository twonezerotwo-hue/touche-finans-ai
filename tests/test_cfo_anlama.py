# -*- coding: utf-8 -*-
"""CFO gunluk Turkce anlayisi: fiil cesitliligi, yazili tutar, goreli tarih, yon, olumsuz/dilek kipi, hesap ipucu, sorgu.

Her tablo satiri: (cumle, beklenen alanlar). ``KAYIT`` = kayit taslagi baslar (karmasik degil),
``YOK`` = kayit BASLAMAZ, ``SORGU`` = okuma sorgusu, ``SOHBET`` = ne kayit ne sorgu.
Bugun = 12.10.2026 (Pazartesi); katalog: test Smart Excel (Gursar Insaat/Mimarlik, Goktürk Okulu, Devrim ...).
"""
import unittest
from datetime import date
from decimal import Decimal as D

import cfo_niyet as N
import cfo_sohbet
import cfo_sorgu
import cfo_yonlendirici
from cfo_test_temeli import AKTOR, BUGUN, CfoTestTemeli

PAY, COL, EXP, REC, ADJ = N.COUNTERPARTY_PAYMENT, N.COLLECTION, N.EXPENSE, N.RECEIVABLE, N.RECEIVABLE_ADJUSTMENT
GIDER = "EXPENSE|PROJECT_COST"          # proje belliyse PROJECT_COST'a incelir

ODEME_FIILLERI = [
    ("Devrim'e 10 bin TL havale yaptım", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'e 10 bin TL elden verdim", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'e 10 bin TL verdik", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'e 10 bin TL gönderdim", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'e 10 bin TL yatırdım", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'e 10 bin lira avans verdik", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'e 10 bin TL nakit ödedim", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim ustaya 10 bin TL ödeme yapıldı", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim'in hakedişinden 10 bin TL ödendi", dict(islem=PAY, tutar=D(10000), cari="Devrim")),   # hakedis KAYNAK, islem odeme
    ("Devrime 10 bin TL ödedik", dict(islem=PAY, cari="Devrim")),
    ("devrim'e 10 bin tl ödedik", dict(islem=PAY, cari="Devrim")),
    ("10 bin TL'yi Devrim'e ödedik", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("Devrim için 10 bin TL ödeme kaydı oluştur", dict(islem=PAY, cari="Devrim")),
    ("Göktürk okulunda Devrim'e 10 bin TL ödeme gir", dict(islem=PAY, cari="Devrim", proje="Göktürk Okulu")),
    ("Devrim'e 10 bin TL daha ödedik", dict(islem=PAY, tutar=D(10000), cari="Devrim")),
    ("DEVRIM'E 20.000 TL ODEME YAPILDI", dict(islem=PAY, tutar=D(20000), cari="Devrim")),
    ("devrime 5000 tl verdim", dict(islem=PAY, tutar=D(5000), cari="Devrim")),
    ("sebahattin cakmaga 7500 tl odeme", dict(islem=PAY, tutar=D(7500), cari="Sebahattin Çakmak")),   # cakmak -> cakmaga (unsuz yumusamasi)
    ("gursar insaat adina devrim usta ya 15 bin lira kasadan odeme yapildi",
     dict(islem=PAY, tutar=D(15000), cari="Devrim", hesap="Gürsar TL Kasa")),
    ("Holding adına Ofis Market'e 2 bin TL ödeme yapıldı", dict(islem=PAY, cari="Ofis Market", faaliyet="touche_holding")),
    ("Sebahattin Çakmak'a dün 12 bin TL elden verdik", dict(islem=PAY, cari="Sebahattin Çakmak", tarih=date(2026, 10, 11))),
    ("bahcesehir villa icin devrime 3 bin 250 tl odedik", dict(islem=PAY, tutar=D(3250), proje="Bahçeşehir Villa")),
    ("Devrim ustanın hakedişine 30 bin TL ödedik", dict(islem=PAY, tutar=D(30000), cari="Devrim")),
    ("1.500 USD Devrim'e ödendi", dict(islem=PAY, tutar=D(1500), cari="Devrim")),
    ("Devrim'e ₺2.500 elden verildi", dict(islem=PAY, tutar=D(2500), cari="Devrim")),
    ("Devrim'e 8 bin TL ödenmiş", dict(islem=PAY, tutar=D(8000), cari="Devrim")),
    ("geçen cuma Sebahattin Çakmak'a 9 bin TL EFT yaptık",
     dict(islem=PAY, tutar=D(9000), cari="Sebahattin Çakmak", tarih=date(2026, 10, 9))),
    ("Dün Devrim'e kasadan 4 bin 500 TL verdik", dict(islem=PAY, tutar=D(4500), tarih=date(2026, 10, 11), hesap="Gürsar TL Kasa")),
]

TUTAR_BICIMLERI = [
    ("Devrim'e on bin lira ödeme yapıldı", D(10000), None), ("Devrim'e yirmi beş bin TL ödedik", D(25000), None),
    ("Devrim'e yüz bin TL ödedik", D(100000), None), ("Devrim'e iki yüz elli bin TL ödedik", D(250000), None),
    ("Devrim'e yarım milyon TL ödedik", D(500000), None), ("Devrim'e bir buçuk milyon TL ödedik", D(1500000), None),
    ("Devrim'e on iki bin beş yüz TL ödedik", D(12500), None), ("Devrim'e üç bin TL ödedik", D(3000), None),
    ("Devrim'e bin lira ödedik", D(1000), None), ("Devrim'e iki bin beş yüz lira ödedik", D(2500), None),
    ("Devrim'e 10 bin 500 TL ödedik", D(10500), None), ("Devrim'e 3 milyon 250 bin TL ödedik", D(3250000), None),
    ("Devrim'e 10bin TL ödedik", D(10000), None), ("Devrim'e 10.000,- TL ödedik", D(10000), None),
    ("Devrim'e 10 000 TL ödedik", D(10000), None), ("Devrim'e 1.5 milyon TL ödedik", D(1500000), None),
    ("Devrim'e 2,5 bin TL ödedik", D(2500), None), ("Devrim'e 12,5 bin TL ödedik", D(12500), None),
    ("Devrim'e 1250,75 TL ödedik", D("1250.75"), None), ("Devrim'e 1.250,75 TL ödedik", D("1250.75"), None),
    ("Devrim'e 10 bin TL'lik ödeme yapıldı", D(10000), None),
    ("Devrim'e ₺10.000 ödedik", D(10000), "TL"), ("Devrim'e $500 ödedik", D(500), "USD"),
    ("Devrim'e €750 ödedik", D(750), "EUR"), ("Devrim'e 500 dolar ödedik", D(500), "USD"),
    ("Devrim'e 100 USD ödedik", D(100), "USD"), ("Devrim'e 100 euro ödeme yapıldı", D(100), "EUR"),
    ("Devrim'e 100 EUR ödedik", D(100), "EUR"),
]

GORECE_TARIHLER = [
    ("Devrim'e geçen cuma 10 bin TL ödedik", date(2026, 10, 9)), ("Devrim'e geçen perşembe 10 bin TL ödedik", date(2026, 10, 8)),
    ("Devrim'e 3 gün önce 10 bin TL ödedik", date(2026, 10, 9)), ("Devrim'e iki gün önce 10 bin TL ödedik", date(2026, 10, 10)),
    ("Devrim'e 10 gün önce 5 bin TL ödedik", date(2026, 10, 2)), ("Devrim'e 1 hafta önce 10 bin TL ödedik", date(2026, 10, 5)),
    ("Devrim'e geçen hafta salı 10 bin TL ödedik", date(2026, 10, 6)), ("Devrim'e salı günü 10 bin TL ödedik", date(2026, 10, 6)),
    ("Devrim'e cuma günü 10 bin TL ödedik", date(2026, 10, 9)), ("Devrim'e geçen pazartesi 10 bin ödedik", date(2026, 10, 5)),
    ("Devrim'e ayın 5'inde 10 bin TL ödedik", date(2026, 10, 5)), ("Devrim'e ayın 30'unda 5 bin ödedik", date(2026, 9, 30)),
    ("Devrim'e geçen ay 15'inde 10 bin TL ödedik", date(2026, 9, 15)),
    ("Devrim'e geçen ayın 20'sinde 5 bin TL ödedik", date(2026, 9, 20)), ("Devrim'e bu ayın 3'ünde 5 bin ödedik", date(2026, 10, 3)),
    ("Devrim'e 12.10 tarihinde 10 bin TL ödedik", date(2026, 10, 12)), ("Devrim'e 15.09 tarihinde 5 bin TL ödedik", date(2026, 9, 15)),
    ("Devrim'e 5 Ekim 2026 tarihinde 10 bin TL ödedik", date(2026, 10, 5)), ("Devrim'e 2 Ekim'de 10 bin TL ödedik", date(2026, 10, 2)),
    ("Devrim'e 25 Eylül'de 10 bin TL ödedik", date(2026, 9, 25)), ("Devrim'e 25 Eylül 2025 tarihinde 10 bin TL ödedik", date(2025, 9, 25)),
    ("Devrim'e 2026-09-25 tarihinde 10 bin TL ödedik", date(2026, 9, 25)),
    ("Devrim'e dünkü 10 bin TL ödemeyi gir", date(2026, 10, 11)), ("Devrim'e bu sabah 10 bin TL ödedik", date(2026, 10, 12)),
]

YON_VE_TUR = [
    # tahsilat: musteri/isveren para ODEDI/gonderdi/yatirdi ya da para GELDI
    ("İşverenden 100 bin TL tahsilat geldi", dict(islem=COL, cari="İşveren")),
    ("İşveren 100 bin TL ödedi", dict(islem=COL, cari="İşveren")),
    ("İşverenden 100 bin TL aldık", dict(islem=COL, cari="İşveren")),
    ("İşveren hesabımıza 100 bin TL yatırdı", dict(islem=COL, cari="İşveren")),
    ("İşverenden 100 bin TL EFT geldi", dict(islem=COL, cari="İşveren")),
    ("İşverenin 100 bin TL ödemesi hesaba geçti", dict(islem=COL, cari="İşveren")),
    ("İşveren 50 bin TL gönderdi", dict(islem=COL, cari="İşveren")),
    ("isveren 75 bin tl gonderdi", dict(islem=COL, tutar=D(75000), cari="İşveren")),
    ("Işveren hesaba 40 bin yatırdı", dict(islem=COL, tutar=D(40000), cari="İşveren")),
    ("işverenden hakediş ödemesi geldi 200 bin", dict(islem=COL, tutar=D(200000), cari="İşveren")),
    ("İşverenin 60 bin TL'lik ödemesi bankaya geçti", dict(islem=COL, tutar=D(60000), cari="İşveren")),
    ("İşveren 3 gün önce 45 bin TL gönderdi", dict(islem=COL, tutar=D(45000), tarih=date(2026, 10, 9))),
    ("Göktürk okulu için işverenden 80 bin TL tahsil edildi", dict(islem=COL, tutar=D(80000), cari="İşveren", proje="Göktürk Okulu")),
    ("Göktürk okulundan 100 bin TL tahsil ettik", dict(islem=COL, proje="Göktürk Okulu")),
    ("Göktürk'ten 90 bin TL tahsilat geldi", dict(islem=COL, tutar=D(90000))),
    ("Göktürk Okulu'ndan işverenden 250 bin tahsilat yapıldı", dict(islem=COL, tutar=D(250000), cari="İşveren")),
    # musteriye YONELEN odeme (iade) odemedir
    ("İşverene 10 bin TL iade ödedik", dict(islem=PAY, cari="İşveren")),
    # hakedis / satis faturasi = gelir tahakkuku; alis faturasi = gider
    ("Göktürk okulu için 300 bin TL hakediş yaz", dict(islem=REC, proje="Göktürk Okulu")),
    ("İşverene 150 bin TL fatura kestik", dict(islem=REC)),
    ("İşverene 150 bin TL fatura düzenledik", dict(islem=REC)),
    ("İşverene 500 bin TL hakediş faturası kestik", dict(islem=REC, tutar=D(500000), cari="İşveren")),
    ("Ofis Market'ten 3 bin 500 TL fatura geldi", dict(islem=EXP, cari="Ofis Market", tutar=D(3500))),
    ("Ofis Market 8.500 TL fatura kesti", dict(islem=EXP, tutar=D(8500), cari="Ofis Market")),
    ("Ofis Market'ten 8.500 TL tutarında fatura aldık", dict(islem=EXP, tutar=D(8500), cari="Ofis Market")),
    ("Göktürk okulu için 12 bin TL malzeme aldık", dict(islem=GIDER, proje="Göktürk Okulu")),
    ("Göktürk'te 6 bin TL beton aldık", dict(islem=GIDER, tutar=D(6000))),
    ("Gürsar adına 2 bin TL kırtasiye masrafı", dict(islem=GIDER, faaliyet="gursar_mimarlik")),
    ("Göktürk'e 4 bin 500 lira yemek gideri", dict(islem=GIDER, tutar=D(4500))),
    ("Göktürk'e 8 bin TL mazot masrafı yaz", dict(islem=GIDER)),
    ("Göktürk'te elektrik için 25 bin TL harcadık", dict(proje="Göktürk Okulu", kalem="Elektrik")),
    ("elektrik için 25 bin harcadık", dict(islem=GIDER, tutar=D(25000), kalem="Elektrik")),
    ("gokturk okulu icin elektrik isi 18 bin tl gider", dict(islem=GIDER, tutar=D(18000), kalem="Elektrik")),
    ("Sebahattin Çakmak'tan 40 bin TL malzeme faturası geldi", dict(islem=EXP, cari="Sebahattin Çakmak")),
    # alacak silme
    ("İşveren alacağından 5 bin TL silindi", dict(islem=ADJ)),
    ("İşverenin 5 bin TL borcunu sildik", dict(islem=ADJ)),
    ("İşverenin 10 bin TL alacağını sildik", dict(islem=ADJ, tutar=D(10000))),
    # fiilsiz ama tutar + tanimli varlik: kayit baslar, islem turu sihirbazda sorulur
    ("Devrim 10 bin", dict(tutar=D(10000), cari="Devrim", islem=None)),
    ("Göktürk Okulu 10 bin", dict(tutar=D(10000), proje="Göktürk Okulu", islem=None)),
    ("elden 5 bin TL Devrim'e", dict(tutar=D(5000), cari="Devrim", hesap="Gürsar TL Kasa")),
]

HESAP_IPUCLARI = [
    ("Devrim'e kasadan 10 bin TL ödedik", "Gürsar TL Kasa"), ("Devrim'e elden 10 bin TL verdik", "Gürsar TL Kasa"),
    ("Devrim'e nakit 10 bin TL ödedik", "Gürsar TL Kasa"),
    ("Gürsar'dan Devrim'e bankadan 10 bin lira havale yaptık", "Gürsar TL Banka"),
]

KAYIT_DEGIL = [          # gerceklesmis bir hareketi ANLATMAYAN cumleler: olumsuz / gereklilik / gelecek / istek / soru
    "Devrim'e 10 bin TL ödeme yapmadık", "Devrim'e 10 bin TL ödeme yapılmadı", "Devrim'e 10 bin TL ödenmedi",
    "Devrim'e 10 bin TL ödeyemedik", "Devrim'e 10 bin TL ödemeyeceğiz", "Devrim'e 10 bin TL ödeme yapmam lazım",
    "Devrim'e 10 bin TL ödeme yapılması gerekiyor", "Devrim'e ödeme yapmayı unuttum", "Gürsar'dan Devrim'e 10 bin ödeme yapılmamış",
    "Devrim'e ödeme yapılmadı, 10 bin TL", "Devrim 10 bin TL ödeme bekliyor", "Devrim'e 10 bin TL ödeme yapabiliriz",
    "Devrim'e 10 bin TL ödeme yapmayı düşünüyoruz", "Devrim'e 10 bin TL ödenmesi planlandı", "Devrim 10 bin TL talep etti",
    "devrim usta 10 bin istedi", "devrim'e yarın 10 bin TL ödeyeceğiz", "Devrim'e 10 bin TL ödeme yapsak mı",
    "Devrim'e 10 bin TL ödesek olur mu", "10 bin TL yeter mi", "Devrim'e 10 bin TL ödedik mi?",
    "10 bin TL ödemek gerekirse Devrim'e öderiz", "Devrim'e ödemeyi henüz yapmadık, 10 bin TL",
    "Devrim 10 bin TL ödenmesini istiyor", "Devrim'e 10 bin TL ödenmemiş görünüyor", "Devrim'e 10 bin TL ödeme yapılacak",
    "Devrim'e 10 bin TL ödemeyi ay sonuna bırakalım", "Devrim'e 10 bin TL ödersek Gürsar'ın hesabı yeter mi",
    "Müdüre 3 rapor verdik", "Devrim'e 2 kutu çay verdik",        # 'verdik' genel fiil: guclu tutar (TL/bin/binlik) olmadan odeme degil
]

SORGU_METRIKLERI = [
    ("Devrim'e bu ay ne kadar ödedik", dict(metrik="ODENEN", cari="Devrim")),
    ("Devrim'e şimdiye kadar ne kadar ödedik", dict(metrik="ODENEN", cari="Devrim")),
    ("Devrim'e yaptığımız tüm ödemeler", dict(metrik="ODENEN", cari="Devrim")),
    ("Devrim'in ödemeleri", dict(metrik="ODENEN", cari="Devrim")),
    ("Bu ay Devrim'e ödenen toplam tutar", dict(metrik="ODENEN", cari="Devrim")),
    ("Göktürk'ün kârı ne durumda", dict(metrik="KAR", proje="Göktürk Okulu")),
    ("Göktürk'ün kar zarar durumu", dict(metrik="KAR", proje="Göktürk Okulu")),
    ("Göktürk okulu ne kadar kâr getirdi", dict(metrik="KAR", proje="Göktürk Okulu")),
    ("Ne kadar kâr ettik", dict(metrik="KAR")), ("Zarar ediyor muyuz", dict(metrik="KAR")),
    ("Gelir gider farkımız ne", dict(metrik="KAR")),
    ("Göktürk okulundan kalan alacağımız nedir", dict(metrik="KALAN_ALACAK", proje="Göktürk Okulu")),
    ("İşveren bize ne kadar borçlu", dict(metrik="KALAN_ALACAK")),           # karsi tarafin borcu = bizim alacagimiz
    ("Alacaklarımız ne durumda", dict(metrik="KALAN_ALACAK")), ("Alacak durumumuz nasıl", dict(metrik="KALAN_ALACAK")),
    ("Devrim'e borcumuz var mı", dict(metrik="ACIK_BORC", cari="Devrim")),
    ("Devrim ne kadar alacaklı", dict(metrik="ACIK_BORC", cari="Devrim")),   # O alacakli = biz borcluyuz
    ("Toplam kaç TL borcumuz var", dict(metrik="ACIK_BORC")),
    ("Göktürk'e ne kadar masraf yaptık", dict(metrik="GIDER", proje="Göktürk Okulu")),
    ("Holding'in toplam gideri ne kadar", dict(metrik="GIDER")),
    ("Göktürk'ten ne kadar tahsil ettik", dict(metrik="TAHSIL", proje="Göktürk Okulu")),
    ("Gelirimiz ne kadar", dict(metrik="KAZANC")), ("gokturk okulunun cirosu ne", dict(metrik="KAZANC", proje="Göktürk Okulu")),
    ("Ekim ayında Göktürk'e ne kadar harcadık", dict(metrik="ODENEN|GIDER", proje="Göktürk Okulu")),
    ("Net nakit durumumuz nedir", dict(metrik="NET_NAKIT")),
    ("Gürsar'ın bu ayki ödemelerini listele", dict(metrik="ODENEN", faaliyet="gursar_mimarlik")),
    ("Devrim'e toplam ne kadar borcumuz kaldı", dict(metrik="ACIK_BORC", cari="Devrim")),
    ("Göktürk okulunun gider toplamı", dict(metrik="GIDER", proje="Göktürk Okulu")),
    ("Geçen ayki giderlerimiz", dict(metrik="GIDER")), ("Gürsar'ın kârlılığı nasıl", dict(metrik="KAR", faaliyet="gursar_mimarlik")),
    ("Bahçeşehir villaya ne kadar harcandı", dict(metrik="ODENEN|GIDER", proje="Bahçeşehir Villa")),
    ("Sebahattin Çakmak'a ödediklerimizi göster", dict(metrik="ODENEN", cari="Sebahattin Çakmak")),
    ("Holding bu yıl ne kadar kazandı", dict(metrik="KAZANC")),
    ("Gürsar'ın açık borçları neler", dict(metrik="ACIK_BORC", faaliyet="gursar_mimarlik")),
    # yon: musteri/isveren ODEDI = bizim tahsilatimiz; musteriye YONELEN odeme (iade) odemedir
    ("İşveren Göktürk için ne kadar ödedi", dict(metrik="TAHSIL", cari="İşveren")),
    ("İşverenin ödemeleri", dict(metrik="TAHSIL", cari="İşveren")),
    ("İşverenden bu ay ne kadar tahsilat geldi", dict(metrik="TAHSIL", cari="İşveren")),
    ("İşverene ne kadar ödedik", dict(metrik="ODENEN", cari="İşveren")),
]

SORGU_DONEMLERI = [
    ("Bu hafta ne kadar ödeme yaptık", date(2026, 10, 12), date(2026, 10, 18)),
    ("Geçen hafta ne kadar tahsilat yaptık", date(2026, 10, 5), date(2026, 10, 11)),
    ("Bugün ne kadar ödeme yaptık", date(2026, 10, 12), date(2026, 10, 12)),
    ("Dün ne kadar ödeme yapıldı", date(2026, 10, 11), date(2026, 10, 11)),
    ("Bu yıl toplam ne kadar gider yaptık", date(2026, 1, 1), date(2026, 12, 31)),
    ("Geçen yıl ne kadar kazandık", date(2025, 1, 1), date(2025, 12, 31)),
    ("Son 30 günde toplam ne kadar ödeme yaptık", date(2026, 9, 13), date(2026, 10, 12)),
    ("Son 3 ayda ne kadar ödeme yaptık", date(2026, 7, 13), date(2026, 10, 12)),
    ("1 Ekim ile 10 Ekim arasında ne kadar ödeme yaptık", date(2026, 10, 1), date(2026, 10, 10)),
    ("İlk çeyrekte ne kadar gider yaptık", date(2026, 1, 1), date(2026, 3, 31)),
    ("Eylül'de Devrim'e ne kadar ödeme yaptık", date(2026, 9, 1), date(2026, 9, 30)),
    ("Ekimde Devrim'e ne kadar ödedik", date(2026, 10, 1), date(2026, 10, 31)),
    ("Eylülde Göktürk'e ne kadar harcadık", date(2026, 9, 1), date(2026, 9, 30)),
    ("Eylül ayı tahsilatları", date(2026, 9, 1), date(2026, 9, 30)),
    ("2025 yılında ne kadar gider yaptık", date(2025, 1, 1), date(2025, 12, 31)),
    ("Bu hafta Göktürk'te ne kadar masraf yaptık", date(2026, 10, 12), date(2026, 10, 18)),
    ("Devrim'e son 3 ayda ne kadar ödeme yaptık", date(2026, 7, 13), date(2026, 10, 12)),
    ("Gürsar'ın bu ay ne kadar tahsilatı var", date(2026, 10, 1), date(2026, 10, 31)),
    ("Bu ay kaç TL tahsilat yaptık", date(2026, 10, 1), date(2026, 10, 31)),
    ("Eylül ayında toplam kaç ödeme yaptık", date(2026, 9, 1), date(2026, 9, 30)),
    ("Geçen ayki giderlerimiz", date(2026, 9, 1), date(2026, 9, 30)),
    ("Holding bu yıl ne kadar kazandı", date(2026, 1, 1), date(2026, 12, 31)),
]

SOHBET_CUMLELERI = ["kolay gelsin", "sağ ol", "merhaba", "teşekkürler", "ne yapabilirsin", "günaydın", "selam", "nasılsın", "tamam", "Excel'i aç",
                    "10 bin TL", "Devrim", "ödeme"]


def _ad(v):
    return v.ad if v is not None else None


class GunlukDilAnlayisiTestleri(CfoTestTemeli):
    def _kayit(self, metin):
        n = self.niyet(metin)
        self.assertEqual(n.tur, "KAYIT", metin)
        self.assertFalse(n.karmasik, f"{metin}: {n.karmasik_nedeni}")
        self.assertFalse(n.desteklenmeyen, metin)
        return n

    def _alanlar(self, n, metin, **bek):
        if "islem" in bek:
            self.assertIn(n.islem, bek["islem"].split("|") if bek["islem"] else [None], f"{metin}: islem={n.islem}")
        if "tutar" in bek:
            self.assertEqual(n.tutar, bek["tutar"], metin)
        if "cari" in bek:
            self.assertEqual(_ad(n.cari), bek["cari"], metin)
        if "proje" in bek:
            self.assertEqual(_ad(n.proje), bek["proje"], metin)
        if "kalem" in bek:
            self.assertEqual(_ad(n.kalem), bek["kalem"], metin)
        if "hesap" in bek:
            self.assertEqual(_ad(n.hesap), bek["hesap"], metin)
        if "faaliyet" in bek:
            self.assertEqual(n.faaliyet.kimlik if n.faaliyet else None, bek["faaliyet"], metin)
        if "tarih" in bek:
            self.assertEqual((n.tarih, n.tarih_kaynagi), (bek["tarih"], "metin"), metin)

    def test_odeme_fiilleri_ve_yazim_bicimleri(self):
        for metin, bek in ODEME_FIILLERI:
            with self.subTest(metin=metin):
                self._alanlar(self._kayit(metin), metin, **bek)

    def test_tutar_bicimleri_yazili_parcali_ve_sembollu(self):
        for metin, tutar, pb in TUTAR_BICIMLERI:
            with self.subTest(metin=metin):
                n = self._kayit(metin)
                self.assertEqual(n.tutar, tutar, metin)
                if pb:
                    self.assertEqual(n.pb, pb, metin)

    def test_yazili_tutar_denetim_icin_orijinal_metni_korur(self):
        n = self._kayit("Devrim'e on bin lira ödeme yapıldı")
        self.assertEqual(n.metin, "Devrim'e on bin lira ödeme yapıldı")        # audit'e giden talimat degismez
        self.assertEqual(n.tutar, D(10000))

    def test_goreli_tarihler(self):
        for metin, beklenen in GORECE_TARIHLER:
            with self.subTest(metin=metin):
                n = self._kayit(metin)
                self.assertEqual((n.tarih, n.tarih_kaynagi), (beklenen, "metin"), metin)

    def test_tarih_sayisi_tutar_sanilmaz(self):
        n = self._kayit("Devrim'e ayın 5'inde 500 ödedik")
        self.assertEqual((n.tutar, n.tarih), (D(500), date(2026, 10, 5)))
        self.assertFalse(n.coklu_tutar)

    def test_gecersiz_goreli_tarih_tahmin_edilmez(self):
        n = self._kayit("Devrim'e 31'inde 5 bin TL ödedik")           # bu ay 31 gunden once gecmis ay: Eylul'de 31 yok
        self.assertIsNone(n.tarih)
        self.assertTrue(n.tarih_hatasi)

    def test_yon_ve_islem_turu(self):
        for metin, bek in YON_VE_TUR:
            with self.subTest(metin=metin):
                n = self.niyet(metin)
                self.assertEqual(n.tur, "KAYIT", metin)
                self.assertFalse(n.karmasik, f"{metin}: {n.karmasik_nedeni}")
                self._alanlar(n, metin, **bek)

    def test_musteri_para_odediyse_tahsilat_notu_denetlenir(self):
        n = self._kayit("İşveren 100 bin TL ödedi")
        self.assertTrue(any("tahsilat olarak anlaşıldı" in x for x in n.notlar), n.notlar)

    def test_hesap_ipuclari(self):
        for metin, hesap in HESAP_IPUCLARI:
            with self.subTest(metin=metin):
                n = self._kayit(metin)
                self.assertEqual(_ad(n.hesap), hesap, metin)
                self.assertTrue(any("ipucundan çıkarıldı" in x for x in n.notlar), n.notlar)

    def test_hesap_ipucu_belirsizse_tahmin_edilmez(self):
        n = self._kayit("Devrim'e bankadan 10 bin TL havale yaptık")      # faaliyet bilinmiyor: iki TL banka (Gursar/Holding)
        self.assertIsNone(n.hesap)
        self.assertEqual(n.hesap_ipucu, "BANKA")                          # ipucu sihirbaza tasinir

    def test_kayit_degil_olumsuz_gereklilik_gelecek_istek(self):
        for metin in KAYIT_DEGIL:
            with self.subTest(metin=metin):
                n = self.niyet(metin)
                self.assertFalse(n.tur == "KAYIT" and not n.karmasik, f"{metin} kayit baslatti: {n}")

    def test_olumsuz_ve_gereklilik_nedeni_isaretlenir(self):
        self.assertEqual(self.niyet("Devrim'e 10 bin TL ödeme yapmadık").reddedilen, "olumsuz ifade")
        self.assertEqual(self.niyet("Devrim'e 10 bin TL ödeme yapmam lazım").reddedilen, "gereklilik/istek ifadesi")

    def test_nazik_komut_soru_degil_kayittir(self):
        n = self.niyet("Devrim'e 10 bin TL ödeme ekler misin?")
        self.assertEqual((n.tur, n.tutar, _ad(n.cari)), ("KAYIT", D(10000), "Devrim"))

    def test_yazim_hatasi_ve_kismi_ad_oneridir_otomatik_secilmez(self):
        n = self.niyet("Gökturk okulunda Devirm'e 10 bin ödeme yapıldı")      # harf yer degistirmesi
        self.assertIsNone(n.cari)
        self.assertEqual([_ad(x) for x in n.oneri["cari"]], ["Devrim"])
        n = self.niyet("Çakmak'a 5 bin TL ödedik")                            # tek kismi aday: yine onay ister
        self.assertIsNone(n.cari)
        self.assertEqual([_ad(x) for x in n.oneri["cari"]], ["Sebahattin Çakmak"])

    def test_proje_kismi_adi_oneridir(self):
        for metin in ("Göktürk için işverenden 100 bin TL para girdi", "Göktürk için işverene 150 bin TL hakediş çıkardık"):
            with self.subTest(metin=metin):
                n = self._kayit(metin)
                self.assertIn("Göktürk Okulu", [_ad(x) for x in n.oneri.get("proje", [])] + [_ad(n.proje)])

    def test_sohbet_kayit_veya_sorgu_degildir(self):
        for metin in SOHBET_CUMLELERI:
            with self.subTest(metin=metin):
                self.assertEqual(self.niyet(metin).tur, "BELIRSIZ", metin)

    def test_sorgu_metrik_ve_kapsam(self):
        k = self.katalog()
        for metin, bek in SORGU_METRIKLERI:
            with self.subTest(metin=metin):
                self.assertEqual(N.analiz_et(metin, k, BUGUN).tur, "SORGU", metin)
                s = cfo_sorgu.sorgu_coz(metin, k, BUGUN)
                self.assertIsNotNone(s, f"{metin}: metrik cikmadi")
                self.assertIn(s.metrik, bek["metrik"].split("|"), metin)
                if "cari" in bek:
                    self.assertEqual(_ad(s.cari), bek["cari"], metin)
                if "proje" in bek:
                    self.assertEqual(_ad(s.proje), bek["proje"], metin)
                if "faaliyet" in bek:
                    self.assertEqual(s.faaliyet.kimlik if s.faaliyet else None, bek["faaliyet"], metin)

    def test_sorgu_donemleri(self):
        k = self.katalog()
        for metin, bas, bit in SORGU_DONEMLERI:
            with self.subTest(metin=metin):
                s = cfo_sorgu.sorgu_coz(metin, k, BUGUN)
                self.assertIsNotNone(s, metin)
                self.assertEqual((s.bas, s.bit), (bas, bit), f"{metin}: {s.donem_etiketi}")

    def test_sorguda_kismi_proje_adi_kabul_edilir_ve_notla_gosterilir(self):
        s = cfo_sorgu.sorgu_coz("Göktürk'e ne kadar masraf yaptık", self.katalog(), BUGUN)
        self.assertEqual(_ad(s.proje), "Göktürk Okulu")
        self.assertTrue(any("Göktürk Okulu olarak anlaşıldı" in x for x in s.notlar), s.notlar)

    def test_sorguda_kisi_adi_sessizce_secilmez(self):
        s = cfo_sorgu.sorgu_coz("Çakmak'a ne kadar ödedik", self.katalog(), BUGUN)
        self.assertIsNone(s.cari)
        self.assertEqual([_ad(x) for x in s.belirsiz["cari"]], ["Sebahattin Çakmak"])


class DilAnlayisiAkisTestleri(CfoTestTemeli):
    """Anlasilan cumle sihirbaza, yonlendiriciye ve denetim kaydina dogru akar."""

    def _isle(self, soru, **body):
        return cfo_yonlendirici.sohbet_isle({"soru": soru, **body}, {"kimlik": AKTOR}, "OWNER", self.katalog(), BUGUN)

    def test_yazili_tutarla_taslak_orijinal_talimat_denetimde(self):
        y = self.baslat("Devrim'e on bin lira ödeme yapıldı")
        self.assertEqual(y["cfo"]["alanlar"]["tutar"], "10.000 TL")
        olay = self.audit_olaylari("cfo_taslak_olustu")[-1]
        self.assertEqual(olay["orijinal_talimat"], "Devrim'e on bin lira ödeme yapıldı")
        self.assertEqual(olay["tutar"], "10000")

    def test_sihirbaz_yazili_tutar_cevabini_anlar(self):
        y = self.baslat("Gürsar Mimarlık Göktürk Okulu Devrim'e ödeme yapıldı")
        self.assertEqual(y["cfo"]["soru"]["alan"], "tutar")
        y = cfo_sohbet.metinle_cevapla(y["cfo"]["taslak_id"], "yirmi beş bin TL", AKTOR, self.katalog())
        self.assertEqual(y["cfo"]["alanlar"]["tutar"], "25.000 TL")

    def test_banka_ipucu_faaliyet_sonradan_belli_olunca_hesabi_secer(self):
        y = self.baslat("Devrim'e bankadan 10 bin TL havale yaptık")
        self.assertEqual(y["cfo"]["soru"]["alan"], "faaliyet")
        sorulanlar = ["faaliyet"]
        y = self.sec(y, "faaliyet", "gursar_mimarlik")
        while y["cfo"].get("soru"):
            alan = y["cfo"]["soru"]["alan"]
            sorulanlar.append(alan)
            y = self.sec(y, alan, {"proje": "Göktürk Okulu", "islem": N.COUNTERPARTY_PAYMENT, "cari": "Devrim"}[alan])
        self.assertNotIn("hesap", sorulanlar)                              # 'bankadan' tek uygun hesabi belirler
        kayit = y["kayit_taslagi"]["kayitlar"][0]
        self.assertEqual(kayit["hesap_kasa"], "Gürsar TL Banka")
        t = cfo_sohbet.getir(y["cfo"]["taslak_id"])
        self.assertTrue(t["hesap_kaynagi"].startswith("otomatik"), t["hesap_kaynagi"])      # gerekce taslakta gorunur

    def test_olumsuz_cumle_taslak_acmaz_eski_hatta_birakilir(self):
        for soru in ("Devrim'e 10 bin TL ödeme yapmadık", "Devrim'e 10 bin TL ödeme yapmam lazım"):
            with self.subTest(soru=soru):
                self.assertIsNone(self._isle(soru))
        self.assertEqual(self.audit_olaylari("cfo_taslak_olustu"), [])

    def test_fatura_kestik_gelir_tahakkuku_taslagi_olur(self):
        y = self._isle("İşverene 150 bin TL fatura kestik")
        self.assertEqual(y["niyet"], "cfo_sihirbaz")
        self.assertEqual(y["cfo"]["alanlar"]["islem_turu"], "Hakediş / Gelir Tahakkuku")

    def test_isveren_odedi_tahsilat_taslagi_olur_odeme_degil(self):
        y = self._isle("Göktürk okulunda İşveren 100 bin TL ödedi")
        self.assertEqual(y["cfo"]["alanlar"]["islem_turu"], "Tahsilat")


if __name__ == "__main__":
    unittest.main()
