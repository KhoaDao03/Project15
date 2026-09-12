import hashlib
import json
from dataclasses import asdict, replace

from fastapi.testclient import TestClient
from test_execution import decision, ready

from btc15.config import Settings, Strategy
from btc15.dashboard import create_app
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


def test_settings_save_validation_and_new_session_only(store, tmp_path):
    settings = Settings(data_dir=str(tmp_path))
    original = Strategy()
    with TestClient(create_app(store, settings=settings, config=original)) as client:
        saved = client.get("/api/strategy").json()["config"]
        saved.update(enabled=False, min_probability=0.95)
        response = client.put("/api/strategy", json=saved)
        assert response.status_code == 200
        assert Strategy.load(tmp_path / "strategy.json").enabled is False
        data = client.get("/api/strategy").json()
        assert data["session_version"] == original.version
        assert data["version"] != original.version
        for change in ({"min_probability": 2}, {"enabled": "false"}, {"no_new_entry": 900}, {"unknown": 1}):
            assert client.put("/api/strategy", json={**saved, **change}).status_code == 422
        assert (
            client.put(
                "/api/strategy", json=saved, headers={"Origin": "https://unrelated.example"}
            ).status_code
            == 403
        )
        assert client.put("/api/strategy", content="{}").status_code == 415
        assert client.get("/api/strategy").json()["config"] == saved
    with TestClient(create_app(store, settings=settings)) as client:
        data = client.get("/api/strategy").json()
        assert data["session_version"] == data["version"]


def test_disabled_strategy_blocks_candidates_and_submission(store, market, book, config):
    now = market.close_time - 300
    disabled = replace(config, enabled=False)
    result = evaluate(
        market,
        book,
        Tick(now, now, market.spec.strike + 100),
        dict(volatility_disagreement=0, regime="NORMAL"),
        dict(conservative_yes=0.99),
        dict(score=100, reasons=[]),
        now,
        disabled,
    )
    assert result["decision"] == "NO_TRADE"
    assert "STRATEGY_DISABLED" in [r["code"] for r in result["reasons"]]
    executor = ready(store, market, now, disabled)
    assert executor.submit(market, book, decision(), "op", now, True) is None
    assert not store.list(kind="order")


def test_enabled_default_preserves_historical_config_version():
    config = Strategy()
    historical = asdict(config)
    for name in (
        "enabled",
        "daily_entry_limits_enabled",
        "resting_limit_recheck",
        "revalidate_entry_signal",
        "max_entry_retries",
        "entry_retry_cooldown",
        "hold_value_exit_enabled",
        "profit_value_exit_enabled",
        "entry_probability_deductions",
        "sustained_lead_enabled",
        "late_entry_enabled",
        "late_no_new_entry",
        "lead_confirmation_samples",
        "late_min_probability",
        "late_lead_confirmation_samples",
        "min_lead_sigma",
        "late_min_lead_sigma",
        "bollinger_entry_filter_enabled",
    ):
        del historical[name]
    expected = hashlib.sha256(json.dumps(historical, sort_keys=True).encode()).hexdigest()[:16]
    assert config.version == expected
    assert replace(config, enabled=False).version != expected


def test_cli_uses_saved_settings_unless_explicit_config(tmp_path, monkeypatch, capsys):
    import sys

    from btc15 import cli

    saved = replace(Strategy(), enabled=False)
    (tmp_path / "strategy.json").write_text(json.dumps(asdict(saved)))
    monkeypatch.setattr(cli.Settings, "env", lambda: Settings(data_dir=str(tmp_path)))
    monkeypatch.setattr(sys, "argv", ["btc15", "config"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["enabled"] is False
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps(asdict(Strategy())))
    monkeypatch.setattr(sys, "argv", ["btc15", "--config", str(explicit), "config"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["enabled"] is True
