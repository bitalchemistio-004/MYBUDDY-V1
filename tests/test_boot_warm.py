"""Everything a turn needs is loaded at boot, one after another (M2 S4, G1)."""
from core import boot_warm


def test_parts_load_one_after_another_and_a_failure_doesnt_stop_the_rest(monkeypatch):
    order, logged = [], []

    def failing():
        order.append("memory search")
        raise RuntimeError("no index")
    monkeypatch.setattr(boot_warm, "STEPS", (("voice", lambda: order.append("voice")), ("memory search", failing),
                                            ("hearing", lambda: order.append("hearing")),
                                            ("brain", lambda: order.append("brain") or "")))
    monkeypatch.setattr(boot_warm.log, "info", logged.append)
    monkeypatch.setattr(boot_warm.log, "warn", logged.append)
    boot_warm.warm_everything()
    assert order == ["voice", "memory search", "hearing", "brain"]
    assert any("memory search failed to load" in m for m in logged)
    assert logged[-1].startswith("[Boot] ready in")

