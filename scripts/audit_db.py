# -*- coding: utf-8 -*-
"""Veritabani tabanli denetim kaydi (audit log).

actor / action / entityType / entityId / before / after / timestamp alanlarini
tutar. Hassas alanlar (parola, secret, api key, token) loglanmaz. Ayrica
mevcut hash-zincirli JSONL denetim kaydina da yazar.
"""

from __future__ import annotations

import json
from datetime import datetime

from guvenlik import audit_yaz as _zincir_audit
from veri_katmani import baglanti, veritabani_hazirla

HASSAS = ("parola", "password", "secret", "api_key", "apikey", "token", "private", "key")


def _temizle(veri):
    """Sozluk icindeki hassas alanlari maskeler (ic ice)."""
    if isinstance(veri, dict):
        return {k: ("***" if any(h in str(k).casefold() for h in HASSAS) else _temizle(v))
                for k, v in veri.items()}
    if isinstance(veri, list):
        return [_temizle(x) for x in veri]
    return veri


def kaydet(action: str, *, actor=None, entity_type=None, entity_id=None,
           before=None, after=None, **ek) -> None:
    """Denetim kaydini DB'ye ve hash zincirine yazar."""
    action = str(action or "").strip()
    if not action:
        raise ValueError("Denetim eylemi gerekli.")
    onceki = json.dumps(_temizle(before), ensure_ascii=False, default=str) if before is not None else None
    sonraki = json.dumps(_temizle(after), ensure_ascii=False, default=str) if after is not None else None
    zaman = datetime.now().isoformat(timespec="seconds")
    veritabani_hazirla()
    with baglanti() as con:
        con.execute(
            "INSERT INTO audit_log(aktor,eylem,varlik_tipi,varlik_id,onceki,sonraki,zaman) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(actor) if actor is not None else None, action, entity_type, entity_id,
             onceki, sonraki, zaman),
        )
    # Hash-zincirli JSONL kaydina da yaz (mevcut altyapiyla uyumlu)
    try:
        _zincir_audit(action, aktor=str(actor) if actor else None, varlik_tipi=entity_type,
                      varlik_id=entity_id, **(ek or {}))
    except Exception:  # pragma: no cover - zincir hatasi DB kaydini engellemez
        pass


def kayitlar(*, action=None, entity_type=None, entity_id=None, limit: int = 100) -> list[dict]:
    veritabani_hazirla()
    where, args = [], []
    if action:
        where.append("eylem=?"); args.append(str(action))
    if entity_type:
        where.append("varlik_tipi=?"); args.append(str(entity_type))
    if entity_id:
        where.append("varlik_id=?"); args.append(str(entity_id))
    sql = "SELECT id,aktor,eylem,varlik_tipi,varlik_id,onceki,sonraki,zaman FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 2000)))
    with baglanti() as con:
        rows = con.execute(sql, args).fetchall()
    return [dict(r) for r in rows]
