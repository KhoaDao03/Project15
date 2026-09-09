import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Strategy:
    # All strategy defaults are research ASSUMPTIONS, not fitted/optimal values.
    enabled: bool = True
    entry_window_start: int = 480
    no_new_entry: int = 120
    min_entry_price: float = 0.85
    max_entry_price: float = 0.99
    min_probability: float = 0.90
    min_quality: float = 85
    min_edge: float = 0.03
    min_ev: float = 0.03
    max_spread: float = 0.04
    min_liquidity: float = 5
    paths: int = 4000
    seed: int = 15
    warmup_seconds: int = 300
    ewma_decay: float = 0.97
    volatility_floor: float = 0.00001
    shock_threshold: float = 0.003
    extreme_sigma: float = 0.0005
    calibration_penalty: float = 0.02
    slippage: float = 0.002
    reference_max_age: float = 3
    book_max_age: float = 5
    max_clock_skew: float = 2
    evaluation_interval: float = 1
    passive: bool = True
    passive_discount: float = 0.01
    max_wait: float = 20
    latency_seconds: float = 0.25
    queue_multiplier: float = 1.5
    bankroll: float = 1000
    sizing_mode: str = "fixed_contracts"
    fixed_contracts: int = 5
    fixed_dollars: float = 5
    bankroll_fraction: float = 0.01
    max_contracts: int = 10
    max_trade_dollars: float = 10
    max_open_exposure: float = 20
    max_daily_exposure: float = 100
    max_daily_loss: float = 30
    max_daily_trades: int = 20
    stop_multiplier: float = 0.75
    take_profit: float | None = 0.99
    exit_probability: float = 0.70
    min_hold_ev: float = 0
    bollinger_period: int = 20
    bollinger_std: float = 2
    rsi_period: int = 14
    stochastic_period: int = 14
    atr_period: int = 14
    # Current quadratic series fee; live runner additionally checks metadata.
    taker_fee_rate: float = 0.07
    maker_fee_rate: float = 0.0175
    fee_balance_precision: str = "0.0001"

    def __post_init__(self):
        for f in fields(self):
            v = getattr(self, f.name)
            if f.type is int and (type(v) is not int):
                raise ValueError(f"{f.name} requires an integer")
            if f.type is bool and type(v) is not bool:
                raise ValueError(f"{f.name} requires a boolean")
            if f.type is float and (type(v) not in (int, float)):
                raise ValueError(f"{f.name} requires a number")
            if isinstance(v, (float, int)) and (not math.isfinite(v) or v < 0):
                raise ValueError(f"Invalid {f.name}")
        if not 0 < self.no_new_entry < self.entry_window_start <= 900:
            raise ValueError("Invalid entry window")
        if not 0 < self.min_entry_price <= self.max_entry_price < 1:
            raise ValueError("Invalid entry prices")
        if self.paths < 100 or self.warmup_seconds < 30 or not 0 < self.ewma_decay < 1:
            raise ValueError("Insufficient model settings")
        for name in ("min_probability", "calibration_penalty", "exit_probability", "bankroll_fraction"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(name)
        if self.min_quality > 100 or self.queue_multiplier < 1 or self.evaluation_interval <= 0:
            raise ValueError("Invalid quality, queue or evaluation interval")
        if self.sizing_mode not in ("fixed_contracts", "fixed_dollars", "bankroll_percentage"):
            raise ValueError("Unknown sizing mode")
        if self.fee_balance_precision not in ("0.01", "0.0001"):
            raise ValueError("Unknown balance precision")
        if self.take_profit is not None and not 0 < self.take_profit < 1:
            raise ValueError("Invalid take profit")
        if min(self.bollinger_period, self.rsi_period, self.stochastic_period, self.atr_period) < 2:
            raise ValueError("Invalid indicator periods")

    @property
    def version(self):
        values = asdict(self)
        # Enabled is the historical behavior; keep existing checkpoint versions compatible.
        if values["enabled"]:
            del values["enabled"]
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]

    @classmethod
    def load(cls, path=None):
        return cls(**json.loads(Path(path).read_text())) if path else cls()
