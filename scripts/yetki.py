# -*- coding: utf-8 -*-
"""Rol ve izin modeli (sunucu tarafi zorunlu kilar).

UI'de gizlemek tek basina yetkilendirme degildir; izinler API/servis
katmaninda `izin_var`/`yetki_gerekli` ile uygulanir.

Roller: OWNER, CFO, FINANCE_MANAGER, ACCOUNTANT, PROJECT_MANAGER, VIEWER.
"""

from __future__ import annotations

ROLLER = ["OWNER", "CFO", "FINANCE_MANAGER", "ACCOUNTANT", "PROJECT_MANAGER", "VIEWER"]

# Domain izinleri
IZINLER = [
    "holding.goruntule", "finans.goruntule", "finans.duzenle",
    "belge.yukle", "belge.onayla", "transfer.yap", "donem.kapat",
    "butce.duzenle", "senaryo.olustur", "kur.duzenle", "ayar.duzenle",
    "rapor.goruntule", "audit.goruntule", "ai.sor",
    "recovery.authorize",
]

_OKUMA = {"holding.goruntule", "finans.goruntule", "rapor.goruntule", "ai.sor"}
_PROJE = _OKUMA | {"belge.yukle"}
_MUHASEBE = _PROJE | {"finans.duzenle", "senaryo.olustur"}
_YONETIM = _MUHASEBE | {"belge.onayla", "transfer.yap", "butce.duzenle", "kur.duzenle", "audit.goruntule"}
_CFO = _YONETIM | {"donem.kapat"}
# Recovery replays are administrative actions, not ordinary accounting edits.
_CFO = _CFO | {"recovery.authorize"}
_TUM = set(IZINLER)

ROL_IZINLERI = {
    "VIEWER": _OKUMA,
    "PROJECT_MANAGER": _PROJE,
    "ACCOUNTANT": _MUHASEBE,
    "FINANCE_MANAGER": _YONETIM,
    "CFO": _CFO,
    "OWNER": _TUM,
}

# Telegram/kurumsal mevcut roller -> yeni roller (geriye donuk uyumluluk)
LEGACY_ESLEME = {"ADMIN": "OWNER", "FINANS": "FINANCE_MANAGER", "ONAYCI": "CFO", "IZLEYICI": "VIEWER"}


def rol_normalize(rol) -> str:
    rol = str(rol or "VIEWER").strip().upper()
    return LEGACY_ESLEME.get(rol, rol if rol in ROLLER else "VIEWER")


def izin_var(rol, izin: str) -> bool:
    return izin in ROL_IZINLERI.get(rol_normalize(rol), set())


def kontrol(rol, izin: str) -> None:
    """Izin yoksa PermissionError yukseltir."""
    if not izin_var(rol, izin):
        raise PermissionError(f"Yetkisiz işlem: {izin} (rol={rol_normalize(rol)})")


def yetki_gerekli(izin: str):
    """Decorator: fonksiyonun `rol` parametresini kontrol eder."""
    import functools

    def sarici(fonksiyon):
        @functools.wraps(fonksiyon)
        def ic(*args, **kwargs):
            rol = kwargs.get("rol")
            kontrol(rol, izin)
            return fonksiyon(*args, **kwargs)

        return ic

    return sarici


def rol_izinleri(rol) -> list[str]:
    return sorted(ROL_IZINLERI.get(rol_normalize(rol), set()))


def rol_listesi() -> list[dict]:
    return [{"rol": r, "izinler": sorted(ROL_IZINLERI[r])} for r in ROLLER]
