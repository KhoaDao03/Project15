import hashlib
import json
from dataclasses import asdict, replace

import pytest
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
        "asset",
        "full_position_execution",
        "enabled",
        "entry_value_filters_enabled",
        "daily_entry_limits_enabled",
        "resting_limit_recheck",
        "revalidate_entry_signal",
        "max_entry_retries",
        "entry_retry_cooldown",
        "post_close_cooldown",
        "one_trade_per_market",
        "hold_value_exit_enabled",
        "profit_value_exit_enabled",
        "standard_cashout_enabled",
        "entry_probability_deductions",
        "bleep_probability_blend_enabled",
        "bleep_probability_only_enabled",
        "both_models_80_enabled",
        "standard_component_min_probability",
        "late_component_min_probability",
        "fixed_stop_price",
        "project15_probability_veto_enabled",
        "bleep_exchange_seed_enabled",
        "bleep_safety_clamp_enabled",
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


def test_cashout_and_post_close_config_preserve_old_versions_and_validate():
    config = Strategy()
    assert config.version == "1766c001ffaa6835"
    active = Strategy.load("config/settlement-edge-active-paper.json")
    assert active.standard_cashout_enabled is False
    assert active.min_entry_price == 0.80
    assert active.max_entry_price == 0.95
    assert active.post_close_cooldown == 60
    assert active.entry_retry_cooldown == 0
    assert active.exit_probability == 0.0
    historical = replace(
        active,
        standard_cashout_enabled=False,
        min_entry_price=0.70,
        entry_value_filters_enabled=True,
        bleep_probability_blend_enabled=False,
        bleep_probability_only_enabled=False,
        both_models_80_enabled=False,
        standard_component_min_probability=0.80,
        late_component_min_probability=0.80,
        min_probability=0.80,
        min_lead_sigma=1.0,
        late_min_lead_sigma=2.0,
        full_position_execution=False,
        fixed_stop_price=0,
        project15_probability_veto_enabled=True,
        entry_window_start=420,
        lead_confirmation_samples=3,
        late_lead_confirmation_samples=2,
        entry_retry_cooldown=5,
        min_edge=0.01,
        min_ev=0.01,
        bollinger_entry_filter_enabled=True,
        bleep_exchange_seed_enabled=False,
        bleep_safety_clamp_enabled=False,
        post_close_cooldown=0,
        exit_probability=0.70,
        take_profit=0.99,
        one_trade_per_market=False,
    )
    assert historical.version == "8eb9db7ebe0ad470"
    assert replace(config, standard_cashout_enabled=True).version != config.version
    assert replace(config, post_close_cooldown=60).version != config.version
    for value in ("true", 1, None):
        with pytest.raises(ValueError, match="standard_cashout_enabled requires a boolean"):
            replace(config, standard_cashout_enabled=value)
    for value in (-1, float("inf"), float("nan"), "60", True, None):
        with pytest.raises(ValueError, match="post_close_cooldown"):
            replace(config, post_close_cooldown=value)


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


def test_all_crypto_presets_allow_one_sample_and_immediate_retries():
    for asset in ["active", "eth", "sol", "xrp"]:
        c = Strategy.load(f"config/settlement-edge-{asset}-paper.json")
        assert c.entry_window_start == 480
        assert c.confirmation_count() == c.confirmation_count(True) == 1
        assert c.entry_retry_cooldown == 0
        assert c.max_entry_retries == 2
        assert c.sustained_lead_enabled and c.entry_cutoff == 15
