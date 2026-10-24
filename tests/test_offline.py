"""Runtime must touch no network except local Ollama (AGENTS.md rule)."""
import re
import socket
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "scache"
LOCAL = {"127.0.0.1", "::1", "localhost"}


def block_remote_sockets(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if self.family != socket.AF_UNIX and host not in LOCAL:
            raise AssertionError(f"non-local network call to {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("DNS lookup attempted")))


def test_guard_blocks_remote_connect(monkeypatch):
    block_remote_sockets(monkeypatch)
    with pytest.raises(AssertionError, match="non-local"):
        socket.socket().connect(("93.184.216.34", 80))


def test_source_has_no_remote_urls():
    urls = []
    for path in SRC.glob("*.py"):
        for url in re.findall(r"https?://[^\s\"')]+", path.read_text()):
            if not re.match(r"https?://(localhost|127\.0\.0\.1)", url):
                urls.append((path.name, url))
    assert urls == []


def test_default_ollama_url_is_local():
    from scache.config import Settings
    assert re.match(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", Settings().ollama_url)


def test_embedder_sets_hf_offline():
    assert 'os.environ.setdefault("HF_HUB_OFFLINE", "1")' in (SRC / "embed.py").read_text()
