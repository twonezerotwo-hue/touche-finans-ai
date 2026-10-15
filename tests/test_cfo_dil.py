# -*- coding: utf-8 -*-
"""CFO dil yardimcilari: yazili sayi, goreli tarih, sorgu donemi, kip (olumsuz/gereklilik/gelecek), hesap ipucu.

Calisma kitabi gerektirmez; bugun = 12.10.2026 (Pazartesi).
"""
import unittest
from datetime import date

import cfo_dil as D

BUGUN = date(2026, 10, 12)


class YaziliSayiTestleri(unittest.TestCase):
    def test_yazili_tutarlar_rakama_cevrilir(self):
        for metin, beklenen in (
            ("on bin lira", "10000 lira"),
            ("yirmi beş bin TL", "25000 TL"),
            ("yüz bin TL", "100000 TL"),
            ("iki yüz elli bin TL", "250000 TL"),
            ("yarım milyon TL", "500000 TL"),
            ("bir buçuk milyon TL", "1500000 TL"),
            ("on bin beş yüz lira", "10500 lira"),
            ("bin lira", "1000 lira"),
            ("yüz lira", "100 lira"),
        ):
            with self.subTest(metin=metin):
                self.assertEqual(D.sayilari_cevir(metin), beklenen)

    def test_rakamli_parcali_tutarlar_birlestirilir(self):
        for metin, beklenen in (("10 bin 500 TL", "10500 TL"), ("1 milyon 250 bin TL", "1250000 TL"),
                                ("3 milyon 250 bin TL", "3250000 TL"), ("5 bin 50 TL", "5050 TL")):
            with self.subTest(metin=metin):
                self.assertEqual(D.sayilari_cevir(metin), beklenen)

    def test_tutar_olmayan_sayi_kelimeleri_dokunulmaz(self):
        for metin in ("bir ödeme yaptık", "on kişi geldi", "3 bin 5 gün", "10 bin TL 500 TL", "yüzde on",
                      "10 bin 12 Ekim", "Devrim'e on ödeme"):
            with self.subTest(metin=metin):
                self.assertEqual(D.sayilari_cevir(metin), metin)


class GoreceTarihTestleri(unittest.TestCase):
    def test_goreli_tarihler(self):
        for metin, beklenen in (
            ("geçen cuma", date(2026, 10, 9)), ("geçen perşembe", date(2026, 10, 8)),
            ("3 gün önce", date(2026, 10, 9)), ("bir hafta önce", date(2026, 10, 5)), ("2 ay önce", date(2026, 8, 12)),
            ("geçen hafta salı", date(2026, 10, 6)), ("salı günü", date(2026, 10, 6)),
            ("pazartesi günü", date(2026, 10, 5)),             # bugun Pazartesi: bugunden ONCEKI pazartesi
            ("ayın 5'inde", date(2026, 10, 5)), ("geçen ay 15'inde", date(2026, 9, 15)),
            ("ayın 20'sinde", date(2026, 9, 20)),              # bu ayin 20'si henuz gelmedi: gecmis odakli
            ("12.10 tarihinde", date(2026, 10, 12)), ("dünkü ödeme", date(2026, 10, 11)), ("bu sabah", BUGUN),
        ):
            with self.subTest(metin=metin):
                tarih, _, hata = D.gorece_tarih(metin, BUGUN)
                self.assertEqual((tarih, hata), (beklenen, ""))

    def test_gecersiz_gun_hata_verir_tahmin_etmez(self):
        tarih, _, hata = D.gorece_tarih("31'inde", BUGUN)
        self.assertIsNone(tarih)
        self.assertIn("Geçersiz tarih", hata)

    def test_tarih_gibi_gorunen_tutar_tarih_sayilmaz(self):
        self.assertEqual(D.gorece_tarih("12.10 TL", BUGUN), (None, None, ""))

    def test_tarih_ifadeleri_tutar_taramasi_icin_silinir(self):
        temiz = D.tarih_ifadelerini_sil("Devrim'e ayın 5'inde 500 ödedik 3 gün önce")
        self.assertIn("500", temiz)
        self.assertNotIn("5'inde", temiz)
        self.assertNotIn("gün", temiz)


class DonemTestleri(unittest.TestCase):
    def test_donemler(self):
        t = date
        for metin, bas, bit in (
            ("bugün", t(2026, 10, 12), t(2026, 10, 12)), ("dün", t(2026, 10, 11), t(2026, 10, 11)),
            ("bu hafta", t(2026, 10, 12), t(2026, 10, 18)), ("geçen hafta", t(2026, 10, 5), t(2026, 10, 11)),
            ("bu yıl", t(2026, 1, 1), t(2026, 12, 31)), ("geçen yıl", t(2025, 1, 1), t(2025, 12, 31)),
            ("son 30 günde", t(2026, 9, 13), t(2026, 10, 12)), ("son 3 ayda", t(2026, 7, 13), t(2026, 10, 12)),
            ("son üç ay", t(2026, 7, 13), t(2026, 10, 12)), ("son bir yılda", t(2025, 10, 13), t(2026, 10, 12)),
            ("1 Ekim ile 10 Ekim arasında", t(2026, 10, 1), t(2026, 10, 10)),
            ("1-10 Ekim arası", t(2026, 10, 1), t(2026, 10, 10)),
            ("1 ile 10 Ekim arasında", t(2026, 10, 1), t(2026, 10, 10)),
            ("1 Ekim'den 10 Ekim'e kadar", t(2026, 10, 1), t(2026, 10, 10)),
            ("01.10.2026 - 10.10.2026", t(2026, 10, 1), t(2026, 10, 10)),
            ("ilk çeyrekte", t(2026, 1, 1), t(2026, 3, 31)), ("2. çeyrek", t(2026, 4, 1), t(2026, 6, 30)),
            ("bu çeyrek", t(2026, 10, 1), t(2026, 12, 31)), ("geçen çeyrek", t(2026, 7, 1), t(2026, 9, 30)),
            ("Q3", t(2026, 7, 1), t(2026, 9, 30)), ("ilk 6 ay", t(2026, 1, 1), t(2026, 6, 30)),
            ("yıl başından beri", t(2026, 1, 1), BUGUN), ("ay başından bu yana", t(2026, 10, 1), BUGUN),
            ("Ekimde", t(2026, 10, 1), t(2026, 10, 31)), ("Eylülde", t(2026, 9, 1), t(2026, 9, 30)),
        ):
            with self.subTest(metin=metin):
                sonuc = D.donem_cozumle(metin, BUGUN)
                self.assertIsNotNone(sonuc, metin)
                self.assertEqual(sonuc[:2], (bas, bit))

    def test_taninmayan_donem_none_eski_cozumleyiciye_birakilir(self):
        for metin in ("bu ay", "geçen ay", "Ekim ayında", "2025 yılında", "ne kadar ödedik"):
            with self.subTest(metin=metin):
                self.assertIsNone(D.donem_cozumle(metin, BUGUN))


class KipTestleri(unittest.TestCase):
    def _sade(self, metin):
        return D._sade(metin)

    def test_olumsuz_ifadeler(self):
        for metin in ("ödeme yapmadık", "ödeme yapılmadı", "ödenmedi", "ödeyemedik", "ödemeyeceğiz", "ödemiyoruz",
                      "ödeme yapılmamış", "tahsilat gelmedi", "tahsil edilmedi", "almadık"):
            with self.subTest(metin=metin):
                self.assertTrue(D.olumsuz_mu(self._sade(metin)), metin)

    def test_olumlu_ifadeler_olumsuz_sayilmaz(self):
        for metin in ("ödeme yaptık", "ödeme yapıldı", "ödendi", "10 bin lira verdik", "Devrim'e ödemeler", "ödeme kaydı oluştur"):
            with self.subTest(metin=metin):
                self.assertFalse(D.olumsuz_mu(self._sade(metin)), metin)

    def test_gereklilik_ve_istek(self):
        for metin in ("ödeme yapmam lazım", "ödeme yapılması gerekiyor", "ödeyelim", "ödeme yapabiliriz",
                      "ödeme yapmayı unuttum", "ödeme bekliyor", "10 bin istedi", "ödemeyi planladık", "ödememiz gerek"):
            with self.subTest(metin=metin):
                self.assertTrue(D.gereklilik_mi(self._sade(metin)), metin)

    def test_gerceklesmis_hareket_gereklilik_sayilmaz(self):
        for metin in ("ödeme yaptık", "ödeme planı kapsamında ödendi", "alacağını sildik", "kasadan verdik"):
            with self.subTest(metin=metin):
                self.assertFalse(D.gereklilik_mi(self._sade(metin)), metin)

    def test_gelecek_kipi_ve_alacak_ismi(self):
        for metin in ("ödeyeceğiz", "yapılacak", "verecek", "gönderilecek"):
            self.assertTrue(D.gelecek_mi(self._sade(metin)), metin)
        # "alacak" burada ISIM (receivable); fiil degil
        self.assertFalse(D.gelecek_mi(self._sade("İşveren alacağından 5 bin TL silindi")))


class HesapIpucuTestleri(unittest.TestCase):
    def test_kasa_banka_ve_celiski(self):
        self.assertEqual(D.hesap_ipucu(["devrim", "e", "kasadan", "10"]), "KASA")
        self.assertEqual(D.hesap_ipucu(["elden", "verdik"]), "KASA")
        self.assertEqual(D.hesap_ipucu(["bankadan", "havale"]), "BANKA")
        self.assertEqual(D.hesap_ipucu(["havale", "yaptik"]), "BANKA")
        self.assertEqual(D.hesap_ipucu(["kasadan", "bankaya"]), "")             # celiskili ipucu: tahmin yok
        self.assertEqual(D.hesap_ipucu(["devrim", "10", "odedik"]), "")

    def test_cari_adinda_gecen_banka_ipucu_degildir(self):
        # 'banka' jetonu (konum 2) cari adi olarak tuketildiyse yok sayilir
        self.assertEqual(D.hesap_ipucu(["kredi", "veren", "banka"], yasak=frozenset({2})), "")


if __name__ == "__main__":
    unittest.main()
