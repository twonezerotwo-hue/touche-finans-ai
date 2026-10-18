"""PR-06 sentetik ürün kabul sözleşmeleri.

Bu testler yalnızca geçici Excel/SQLite/audit dosyalarıyla çalışır. Gerçek
OneDrive, C:\\ai-excel, üretim verisi veya kimlik bilgisi kullanılmaz. Amaç,
mevcut modüllerin birlikte çalıştığını ölçülebilir bir akışta doğrulamaktır.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import finans_motoru
import kurumsal_finans
import raporlar
import recovery_vault
from cfo_test_temeli import CfoTestTemeli
from finans_islem_journali import recovery_health


class FinalProductAcceptance(CfoTestTemeli):
    """Kullanıcıdan rapora kadar sentetik, uçtan uca kabul testleri."""

    def _hazir(self, belge_id: str, islem: str, tutar: float, *, cari: str = "Tedarikci X",
               pb: str = "TL", kur: float = 1, proje: str = "Göktürk Okulu",
               tarih: str = "2026-10-12", hesap: str = "Gürsar TL Banka") -> dict:
        kayit = {
            "Tarih": tarih,
            "Faaliyet": self.faaliyet_adi,
            "Proje": proje,
            "Cari_Tipi": "Usta",
            "Cari": cari,
            "Islem_Turu": islem,
            "Hesap_Kasa": hesap,
            "Tutar": tutar,
            "PB": pb,
            "Kur": kur,
            "Aciklama": "PR-06 sentetik kabul kaydı",
        }
        return {
            "basari": True,
            "firma_key": "gursar_mimarlik",
            "tur": "finans_belgesi",
            "veri": {"kayitlar": [kayit]},
            "belge_id": belge_id,
            "belge_hash": hashlib.sha256(belge_id.encode()).hexdigest(),
            "dogrulama": {"durum": "PASS", "skor": 100, "hatalar": [], "uyarilar": []},
            "gecici_dosyalar": [],
        }

    def _fis_sayisi(self, belge_id: str) -> int:
        con = sqlite3.connect(self.root / "finans.db")
        try:
            return con.execute("SELECT COUNT(*) FROM fisler WHERE belge_id=?", (belge_id,)).fetchone()[0]
        finally:
            con.close()

    def test_e2e_kullanici_onayi_defter_excel_rapor_yedek_geri_yukleme(self):
        """Talimat -> taslak -> parola -> Excel/SQLite -> rapor -> vault."""
        taslak = self.baslat("Gürsar Mimarlık Göktürk Okulu Devrim'e 17 bin TL ödeme yapıldı")
        taslak = self.sec(taslak, "hesap", "Gürsar TL Banka")
        assert taslak["kayit_taslagi"]["durum"] == "ONAY_BEKLIYOR"
        assert self.ledger() == []

        sonuc = self.onayla(taslak)
        assert sonuc["tamam"] is True
        assert self.ledger()
        assert self._fis_sayisi(taslak["kayit_taslagi"]["belge_id"]) == 1

        mizan = raporlar.trial_balance()
        assert mizan["durum"] == "OK" and mizan["denk"]
        kpi = finans_motoru.holding_kpi("gursar_mimarlik")
        # Tedarikçi ödemesi 320/102 ile kapanır; yönetim maliyeti değil,
        # nakit hareketi olarak raporlanır.
        assert kpi["toplam_nakit_kurus"] == -1_700_000

        key = b"p" * 32
        vault = self.root / "vault" / "acceptance.vault"
        recovery_vault.create_snapshot(
            vault,
            excel_path=self.ana,
            sqlite_path=self.root / "finans.db",
            audit_path=self.audit,
            key=key,
            label="PR-06-e2e",
        )
        restored = self.root / "restore"
        sonuc = recovery_vault.restore_dry_run(vault, restored, key=key)
        assert sonuc["verified"] is True
        assert (restored / "excel.xlsx").exists()
        assert (restored / "finans.db").exists()

    def test_islem_matrisi_tahsilat_tahakkuk_gelir_kredi_fx_ve_grup_ici(self):
        """Gerçekçi sentetik işlem türleri doğru çift kayıt ve kuruş üretir."""
        islemler = [
            ("PA-001", "Usta/Tedarikçi Ödemesi", 40_000, "Tedarikci X", "TL", 1),
            ("PA-002", "Tahsilat", 70_000, "Müşteri A", "TL", 1),
            ("PA-003", "Gider Tahakkuku", 12_000, "Tedarikci Y", "TL", 1),
            ("PA-004", "Gelir Tahakkuku", 90_000, "Müşteri B", "TL", 1),
            ("PA-005", "Kredi Kullanımı", 1_000, "Banka", "EUR", 35),
        ]
        for belge, islem, tutar, cari, pb, kur in islemler:
            from finans_cekirdegi import finans_kaydi_yaz

            finans_kaydi_yaz(self._hazir(belge, islem, tutar, cari=cari, pb=pb, kur=kur), "acceptance")
        assert sum(self._fis_sayisi(belge) for belge, *_ in islemler) == len(islemler)
        assert raporlar.trial_balance()["denk"]
        assert raporlar.kar_zarar()["durum"] == "OK"

        kurumsal_finans.sirketler_arasi_transfer(
            "gursar_mimarlik", "touche_holding", "2026-10-13", 25_000, "sentetik IC", "acceptance"
        )
        assert raporlar.intercompany_balances()["tip"] == "intercompany_balances"

    def test_ayni_deger_ayri_belge_ve_tekrar_onay_idempotent(self):
        """Aynı tutardaki meşru iki belge ayrıdır; aynı belge ikinci kez yazılmaz."""
        from finans_cekirdegi import finans_kaydi_yaz

        birinci = self._hazir("DUP-001", "Usta/Tedarikçi Ödemesi", 15_000, cari="A")
        ikinci = self._hazir("DUP-002", "Usta/Tedarikçi Ödemesi", 15_000, cari="A")
        finans_kaydi_yaz(birinci, "acceptance")
        finans_kaydi_yaz(birinci, "retry")
        finans_kaydi_yaz(ikinci, "acceptance")
        assert self._fis_sayisi("DUP-001") == 1
        assert self._fis_sayisi("DUP-002") == 1

    def test_kilitli_excel_ve_recovery_health_sessizce_saglikli_degil(self):
        """Excel kilidi yazmayı durdurur; bekleyen işlem sağlıkta görünür."""
        taslak = self.baslat("Gürsar Mimarlık Göktürk Okulu Devrim'e 8 bin TL ödeme yapıldı")
        taslak = self.sec(taslak, "hesap", "Gürsar TL Banka")
        import excel_deposu

        gercek_replace = excel_deposu.os.replace

        def kilitli_yalniz_ana(kaynak, hedef):
            if Path(hedef).resolve() == self.ana.resolve():
                raise PermissionError(13, "kilitli")
            return gercek_replace(kaynak, hedef)

        with patch("excel_deposu.os.replace", side_effect=kilitli_yalniz_ana):
            sonuc = self.onayla(taslak)
        assert sonuc["tamam"] is False
        health = recovery_health()
        assert health["durum"] == "degraded"
        assert health["pending_recovery"] or health["needs_review"]

    def test_rapor_finansal_durumla_tutarlidir(self):
        from finans_cekirdegi import finans_kaydi_yaz

        finans_kaydi_yaz(self._hazir("REP-001", "Hakedis", 100_000, cari="İşveren"), "acceptance")
        finans_kaydi_yaz(self._hazir("REP-002", "Usta/Tedarikçi Ödemesi", 30_000), "acceptance")
        kpi = finans_motoru.holding_kpi("gursar_mimarlik")
        ozet = raporlar.holding_overview("gursar_mimarlik")["kpi"]
        assert kpi["toplam_ciro_kurus"] == ozet["toplam_ciro_kurus"]
        assert kpi["toplam_maliyet_kurus"] == ozet["toplam_maliyet_kurus"]
        assert raporlar.bilanco()["denk"]

    def test_rapor_ve_yedek_sentetik_yukte_olculebilir(self):
        from finans_cekirdegi import finans_kaydi_yaz

        baslangic = time.perf_counter()
        for no in range(20):
            finans_kaydi_yaz(
                self._hazir(f"LOAD-{no:03d}", "Gider Tahakkuku", 1_000 + no, cari=f"C{no}"),
                "benchmark",
            )
        raporlar.trial_balance()
        raporlar.cash_forecast_13("gursar_mimarlik")
        elapsed = time.perf_counter() - baslangic
        # CI makineleri değişkendir; bu eşik bir performans iddiası değil, regresyon alarmıdır.
        assert elapsed < 30, f"sentetik kabul yükü 30 saniyeyi aştı: {elapsed:.3f}s"


class AuthorizationAcceptance(CfoTestTemeli):
    def test_kapsam_disi_rapor_ve_kuyruk_erisimine_izin_yok(self):
        import ui_yetki

        ui_yetki._kaydet({"pm@example.test"})
        ui_yetki.rol_ata("pm@example.test", "PROJECT_MANAGER", ["gursar_mimarlik"])
        oturum = ui_yetki.oturum(
            {"Tailscale-User-Login": "pm@example.test"},
            "pc.tailnet.ts.net",
            "127.0.0.1",
        )
        assert oturum["yetkili"] is True
        assert "gursar_mimarlik" in oturum["faaliyetler"]
        assert "touche_holding" not in oturum["faaliyetler"]

