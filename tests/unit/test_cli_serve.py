from __future__ import annotations

from industrypulse import cli


def test_resolve_serve_port_falls_back_to_next_free(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_stop_old_industrypulse_on_windows", lambda _p: False)
    monkeypatch.setattr(cli, "is_port_available", lambda _h, port: port == 8767)
    assert cli.resolve_serve_port("127.0.0.1", 8765) == 8767


def test_resolve_serve_port_reuses_port_after_stopping_old(monkeypatch) -> None:
    calls = {"stopped": False}

    def stop_old(_port: int) -> bool:
        calls["stopped"] = True
        return True

    monkeypatch.setattr(cli, "_stop_old_industrypulse_on_windows", stop_old)
    monkeypatch.setattr(cli, "is_port_available", lambda _h, _p: calls["stopped"])
    assert cli.resolve_serve_port("127.0.0.1", 8765) == 8765
