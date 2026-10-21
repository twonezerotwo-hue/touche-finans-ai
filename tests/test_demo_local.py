from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import demo_local


def test_ollama_probe_returns_safe_provider(monkeypatch):
    monkeypatch.setattr(demo_local.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    provider, detail = demo_local._ollama_probe()
    assert provider == "deterministic"
    assert detail
