"""Tests for the launcher helpers in run.py.

These matter because ``run.py`` is now reached by double-click, where nobody is
watching a terminal for a traceback.
"""

from __future__ import annotations

import socket

import pytest

import run

HOST = "127.0.0.1"


@pytest.fixture()
def occupied_port():
    """Hold a real port open for the duration of a test."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    yield port
    sock.close()


def test_a_bound_port_is_reported_busy(occupied_port):
    assert run.is_port_free(HOST, occupied_port) is False


def test_an_unbound_port_is_reported_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        port = probe.getsockname()[1]
    # socket now closed, so the port is free again
    assert run.is_port_free(HOST, port) is True


def test_find_free_port_skips_a_busy_one(occupied_port):
    chosen = run.find_free_port(HOST, occupied_port)
    assert chosen is not None
    assert chosen != occupied_port
    assert chosen > occupied_port


def test_find_free_port_returns_the_preferred_port_when_available():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        port = probe.getsockname()[1]
    assert run.find_free_port(HOST, port) == port


def test_find_free_port_gives_up_rather_than_looping_forever(monkeypatch):
    monkeypatch.setattr(run, "is_port_free", lambda host, port: False)
    assert run.find_free_port(HOST, 8000, attempts=3) is None


def test_banner_names_the_version_and_folder():
    text = run.banner(HOST, 8000, 8000)
    assert "Level Stats v" in text
    assert "Serving from" in text
    assert "http://127.0.0.1:8000/" in text
    assert "One player" in text and "Compare a list" in text


def test_banner_explains_itself_when_the_port_moved():
    text = run.banner(HOST, 8003, 8000)
    assert "port 8000 was busy, using 8003" in text
    assert "older copy" in text


def test_banner_stays_quiet_when_the_port_did_not_move():
    assert "was busy" not in run.banner(HOST, 8000, 8000)


def test_browser_opens_only_after_the_server_answers(monkeypatch):
    """The whole point of the helper: never open a tab on a dead port."""
    opened: list[str] = []
    calls = {"n": 0}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection refused")
        return FakeResponse()

    monkeypatch.setattr(run.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(run.time, "sleep", lambda _seconds: None)

    run.open_browser_when_ready("http://127.0.0.1:8000/", timeout=5.0)
    for _ in range(200):
        if opened:
            break
        import time as _t

        _t.sleep(0.01)

    assert opened == ["http://127.0.0.1:8000/"]
    assert calls["n"] == 3  # two refusals, then success


def test_browser_is_not_opened_if_the_server_never_answers(monkeypatch):
    opened: list[str] = []

    def always_fail(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(run.urllib.request, "urlopen", always_fail)
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(run.time, "sleep", lambda _seconds: None)

    run.open_browser_when_ready("http://127.0.0.1:8000/", timeout=0.05)
    import time as _t

    _t.sleep(0.3)
    assert opened == []
