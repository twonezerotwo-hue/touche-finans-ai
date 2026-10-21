"""Güvenli çoklu CFO işlem taslakları.

Bu modül birden fazla bağımsız öneriyi tek bir *inceleme paketi* altında tutar.
Paket oluşturmak, satır düzeltmek veya satırı hazırlamak Excel/SQLite'a yazmaz.
Bir satır yalnızca mevcut ``cfo_sohbet.baslat`` akışına dönüştürülür; gerçek
finansal yazım yine parola korumalı onay kuyruğunda yapılır.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import audit_db
import cfo_intelligence as CI
import cfo_niyet as N
import cfo_sohbet
import cfo_varliklar
import config

_LOCK = threading.RLock()
_PATH = Path(config.LOGLAR) / "cfo_batchleri.json"
_BATCHES: dict[str, dict[str, Any]] = {}


def _load() -> None:
    global _BATCHES
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
        _BATCHES = raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        _BATCHES = {}


def _save() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(_BATCHES, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def _canonical(proposal: dict[str, Any]) -> str:
    keys = ("transaction_type", "activity", "project", "counterparty", "amount", "currency", "transaction_date", "due_date")
    return "|".join(str(proposal.get(key) or "").strip().casefold() for key in keys)


def _fingerprint(proposal: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(proposal).encode("utf-8")).hexdigest()


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _public(batch: dict[str, Any]) -> dict[str, Any]:
    return _copy(batch)


def _find_existing(source_text: str, actor: str) -> dict[str, Any] | None:
    source_hash = hashlib.sha256(source_text.strip().encode("utf-8")).hexdigest()
    for batch in _BATCHES.values():
        if batch.get("actor") == str(actor) and batch.get("source_hash") == source_hash and batch.get("status") == "OPEN":
            return batch
    return None


def create(result: CI.IntelligenceResult, source_text: str, actor: str) -> dict[str, Any]:
    """Create or return an open batch. This operation is intentionally read/metadata-only."""
    with _LOCK:
        _load()
        existing = _find_existing(source_text, actor)
        if existing:
            return _public(existing)
        proposals = [x.as_dict() for x in result.proposals]
        seen: dict[str, str] = {}
        items: list[dict[str, Any]] = []
        for index, proposal in enumerate(proposals, 1):
            fp = _fingerprint(proposal)
            item_id = f"item-{index:02d}"
            duplicate_of = seen.get(fp)
            seen.setdefault(fp, item_id)
            items.append({
                "item_id": item_id,
                "proposal": proposal,
                "fingerprint": fp,
                "status": "NEEDS_REVIEW" if duplicate_of else "PENDING",
                "duplicate_of": duplicate_of,
                "draft_id": None,
                "error": None,
            })
        batch_id = "batch-" + uuid.uuid4().hex[:16]
        source_hash = hashlib.sha256(source_text.strip().encode("utf-8")).hexdigest()
        batch = {
            "batch_id": batch_id,
            "actor": str(actor),
            "source_text": source_text,
            "source_hash": source_hash,
            "status": "OPEN",
            "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "items": items,
        }
        _BATCHES[batch_id] = batch
        _save()
        audit_db.kaydet("cfo_batch_olusturuldu", actor=actor, entity_type="cfo_batch", entity_id=batch_id,
                        after={"item_count": len(items), "source_hash": source_hash})
        return _public(batch)


def get(batch_id: str, actor: str | None = None) -> dict[str, Any]:
    with _LOCK:
        _load()
        batch = _BATCHES.get(str(batch_id))
        if not batch:
            raise KeyError("Çoklu işlem paketi bulunamadı.")
        if actor is not None and batch.get("actor") != str(actor):
            raise PermissionError("Bu çoklu işlem paketi sizin kullanıcınıza ait değil.")
        return _public(batch)


def _item(batch: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in batch.get("items", []):
        if item.get("item_id") == str(item_id):
            return item
    raise KeyError("Çoklu işlem satırı bulunamadı.")


def prepare_item(batch_id: str, item_id: str, actor: str, katalog=None, bugun: date | None = None) -> dict[str, Any]:
    """Turn one proposal into the existing password-gated draft; never post it."""
    with _LOCK:
        _load()
        batch = _BATCHES.get(str(batch_id))
        if not batch:
            raise KeyError("Çoklu işlem paketi bulunamadı.")
        if batch.get("actor") != str(actor):
            raise PermissionError("Bu çoklu işlem paketi sizin kullanıcınıza ait değil.")
        item = _item(batch, item_id)
        if item.get("status") == "DRAFT_READY":
            return _public(item)
        if item.get("status") == "NEEDS_REVIEW":
            raise ValueError("Bu satır mükerrer veya belirsiz; önce düzeltin.")
        proposal = item.get("proposal") or {}
        if proposal.get("missing_fields") or proposal.get("validation_errors"):
            raise ValueError("Bu satırda eksik veya geçersiz bilgi var; önce düzeltin.")
        katalog = katalog or cfo_varliklar.katalog_yukle()
        niyet = N.analiz_et(str(item.get("source_text") or proposal.get("source_text") or ""), katalog, bugun, zorla=True)
        niyet.metin = str(proposal.get("source_text") or niyet.metin)
        if niyet.tur != "KAYIT" or niyet.karmasik or niyet.desteklenmeyen:
            raise ValueError("Satır mevcut güvenli kayıt sihirbazına dönüştürülemedi.")
        sonuc = cfo_sohbet.baslat(niyet, actor, katalog, bugun=bugun)
        taslak = sonuc.get("kayit_taslagi") or sonuc.get("kuyruk")
        if not taslak or not taslak.get("token"):
            # Existing wizard asks for a missing field (for example date or
            # account). Keep that question attached to this item; never turn
            # a partial answer into a financial draft or a write.
            item["status"] = "NEEDS_REVIEW"
            item["error"] = sonuc.get("cevap") or sonuc.get("hata") or "Eksik bilgi gerekiyor."
            item["question"] = (sonuc.get("cfo") or {}).get("soru")
            _save()
            audit_db.kaydet("cfo_batch_satir_incelemeye_alindi", actor=actor,
                            entity_type="cfo_batch_item", entity_id=f"{batch_id}:{item_id}",
                            after={"reason": "wizard_clarification"})
            return _public(item)
        item["status"] = "DRAFT_READY"
        item["draft_id"] = taslak.get("token")
        item["error"] = None
        _save()
        audit_db.kaydet("cfo_batch_satir_hazirlandi", actor=actor, entity_type="cfo_batch_item",
                        entity_id=f"{batch_id}:{item_id}", after={"draft_token": taslak.get("token")})
        return _public(item)


def correct_item(batch_id: str, item_id: str, text: str, actor: str, katalog=None, bugun: date | None = None) -> dict[str, Any]:
    """Replace one item only after deterministic re-analysis; no posting occurs."""
    with _LOCK:
        _load()
        batch = _BATCHES.get(str(batch_id))
        if not batch:
            raise KeyError("Çoklu işlem paketi bulunamadı.")
        if batch.get("actor") != str(actor):
            raise PermissionError("Bu çoklu işlem paketi sizin kullanıcınıza ait değil.")
        item = _item(batch, item_id)
        if item.get("status") == "DRAFT_READY":
            raise ValueError("Onay taslağı hazırlanmış satır doğrudan değiştirilemez; mevcut taslağı düzeltin.")
        katalog = katalog or cfo_varliklar.katalog_yukle()
        result = CI.analyze(str(text), katalog, bugun)
        if len(result.proposals) != 1 or not result.safe_to_draft:
            raise ValueError("Düzeltme tek ve tam bir finansal işlem içermeli.")
        proposal = result.proposals[0].as_dict()
        item.update({"proposal": proposal, "fingerprint": _fingerprint(proposal), "status": "PENDING",
                     "duplicate_of": None, "draft_id": None, "error": None})
        fingerprints = {x.get("fingerprint"): x.get("item_id") for x in batch["items"] if x.get("item_id") != item_id}
        if item["fingerprint"] in fingerprints:
            item["status"] = "NEEDS_REVIEW"
            item["duplicate_of"] = fingerprints[item["fingerprint"]]
        batch["source_text"] = batch.get("source_text", "")
        _save()
        audit_db.kaydet("cfo_batch_satir_duzeltildi", actor=actor, entity_type="cfo_batch_item",
                        entity_id=f"{batch_id}:{item_id}", after={"fingerprint": item["fingerprint"]})
        return _public(item)


def reject_item(batch_id: str, item_id: str, actor: str) -> dict[str, Any]:
    with _LOCK:
        _load()
        batch = _BATCHES.get(str(batch_id))
        if not batch:
            raise KeyError("Çoklu işlem paketi bulunamadı.")
        if batch.get("actor") != str(actor):
            raise PermissionError("Bu çoklu işlem paketi sizin kullanıcınıza ait değil.")
        item = _item(batch, item_id)
        if item.get("status") == "DRAFT_READY":
            raise ValueError("Onay kuyruğuna alınmış satırı mevcut belge akışından reddedin.")
        item["status"] = "REJECTED"
        item["error"] = None
        _save()
        audit_db.kaydet("cfo_batch_satir_reddedildi", actor=actor, entity_type="cfo_batch_item", entity_id=f"{batch_id}:{item_id}")
        return _public(item)


_load()
