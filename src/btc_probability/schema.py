"""Versioned records; timestamps are UTC Unix seconds, amounts are Decimal strings on disk."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Literal

YEAR = 365 * 24 * 60 * 60


def encode(value):
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_DECIMAL")
    return result


@dataclass(frozen=True)
class Config:
    provider: Literal["fixed", "realized", "options"] = "options"
    fixed_sigma: float = 0.20
    paths: int = 20000
    seed: int = 15
    requested_half_width: float = 0.01
    reference_max_age: float = 3
    option_max_age: float = 60
    book_max_age: float = 5
    clock_skew: float = 2
    preferred_expiry_hours: float = 30
    max_option_spread_fraction: float = 0.5
    min_option_size: float = 0.1
    min_strikes: int = 3
    realized_window: int = 180
    realized_max_gap: float = 2
    sensitivity_fraction: float = 0.20
    discount: float = 1.0
    series: str = "KXBTC15M"

    def __post_init__(self):
        if self.provider not in ("fixed", "realized", "options"):
            raise ValueError("Unknown provider")
        if not 100 <= self.paths <= 1000000 or self.seed < 0 or self.min_strikes < 2:
            raise ValueError("Invalid simulation budget/seed/strike support")
        for name in (
            "fixed_sigma",
            "reference_max_age",
            "option_max_age",
            "book_max_age",
            "clock_skew",
            "preferred_expiry_hours",
            "max_option_spread_fraction",
            "min_option_size",
            "realized_window",
            "realized_max_gap",
            "discount",
            "requested_half_width",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid " + name)
        if not 0 <= self.sensitivity_fraction < 1 or self.discount > 1:
            raise ValueError("Invalid sensitivity/discount")


@dataclass(frozen=True)
class ContractSpec:
    series_ticker: str
    market_ticker: str
    event_ticker: str
    target: str | None
    target_source: str
    open_time: float
    trading_close_time: float
    observation_end_time: float
    sample_times: tuple[float, ...]
    reference_index: str
    comparison: str
    equality_yes: bool
    rounding: str | None
    decimal_places: int
    payout: str
    currency: str
    lifecycle: str
    rules_url: str
    rules_hash: str
    metadata_received: float
    verified: bool = False
    synthetic: bool = False
    unresolved: tuple[str, ...] = ()
    version: str = "contract-v1"
    research_tie_policy: str | None = None
    profile_id: str | None = None
    verification: dict = field(default_factory=dict)
    opening_sample_times: tuple[float, ...] = ()
    administrative_expiration_time: float | None = None
    expected_expiration_time: float | None = None
    settled_at: float | None = None

    def __post_init__(self):
        if self.comparison not in (">", ">=", "<", "<="):
            raise ValueError("Unsupported comparison")
        if self.equality_yes != (self.comparison in (">=", "<=")):
            raise ValueError("Contradictory equality")
        if self.rounding not in (None, "ROUND_HALF_EVEN", "ROUND_HALF_UP", "none"):
            raise ValueError("Unsupported rounding")
        if not 0 <= self.decimal_places <= 12:
            raise ValueError("Invalid decimal precision")
        if self.target is not None and decimal(self.target) <= 0:
            raise ValueError("Invalid target")
        if decimal(self.payout) <= 0 or self.currency != "USD":
            raise ValueError("Unsupported payout")
        if any(
            not math.isfinite(t)
            for t in (
                *self.sample_times,
                self.open_time,
                self.trading_close_time,
                self.observation_end_time,
                self.metadata_received,
            )
        ):
            raise ValueError("Nonfinite time")
        if self.sample_times and (
            tuple(sorted(set(self.sample_times))) != tuple(self.sample_times)
            or self.sample_times[-1] > self.observation_end_time
        ):
            raise ValueError("Invalid sample schedule")
        if self.verified and (
            not self.sample_times or self.rounding is None or self.target is None or self.unresolved
        ):
            raise ValueError("Incomplete verified contract")

    def yes(self, value):
        if self.target is None or self.rounding is None:
            raise ValueError("Unresolved settlement rules")
        value = decimal(value)
        if self.rounding != "none":
            value = value.quantize(Decimal(1).scaleb(-self.decimal_places), rounding=self.rounding)
        target = decimal(self.target)
        return {">": value > target, ">=": value >= target, "<": value < target, "<=": value <= target}[
            self.comparison
        ]

    @classmethod
    def from_dict(cls, data):
        return cls(
            **dict(
                data,
                sample_times=tuple(data["sample_times"]),
                unresolved=tuple(data.get("unresolved", [])),
                opening_sample_times=tuple(data.get("opening_sample_times", [])),
            )
        )


@dataclass(frozen=True)
class Reference:
    value: str
    source_time: float
    received_time: float
    index: str = "BRTI"
    source: str = "kalshi-cfbenchmarks-1hz"
    recovered: bool = False

    def __post_init__(self):
        if decimal(self.value) <= 0 or not all(
            math.isfinite(t) for t in (self.source_time, self.received_time)
        ):
            raise ValueError("Invalid reference")


@dataclass
class Forecast:
    market_ticker: str
    forecast_time: float
    mode: str
    provider: str
    config_hash: str
    rules_hash: str
    lineage: list[int]
    series_ticker: str = ""
    event_ticker: str = ""
    lifecycle: str = ""
    rules_url: str = ""
    contract_version: str = "contract-v1"
    observation_end_time: float = 0
    trading_close_time: float = 0
    model_version: str = "arithmetic-average-gbm-v1"
    assumptions_version: str = "zero-carry-365-v1"
    readiness: str = "UNAVAILABLE"
    verification_readiness: str = "UNRESOLVED"
    input_readiness: str = "UNAVAILABLE"
    model_readiness: str = "BLOCKED"
    verification_details: dict = field(default_factory=dict)
    research_diagnostic: dict | None = None
    research_settlement: dict | None = None
    reasons: list[str] = field(default_factory=list)
    probability_kind: str = "pricing_proxy"
    raw_p_yes: float | None = None
    raw_p_no: float | None = None
    calibrated_p_yes: float | None = None
    calibrated_p_no: float | None = None
    calibration_version: str | None = None
    calibration_training_cutoff: float | None = None
    display_probability: float | None = None
    display_provenance: str = "raw settlement-average model; no calibrator"
    model_value_yes: float | None = None
    model_value_no: float | None = None
    display_cents: float | None = None
    discount: float = 1.0
    target: str | None = None
    reference: dict | None = None
    remaining_seconds: float = 0
    known_samples: int = 0
    expected_samples: int = 0
    missing_samples: int = 0
    terminal: dict | None = None
    volatility: dict = field(default_factory=dict)
    integrated_variance: float | None = None
    arithmetic_drift_per_year: float = 0
    settlement: dict | None = None
    sensitivity: dict | None = None
    quotes: dict = field(default_factory=dict)
    comparisons: dict = field(default_factory=dict)
    input_ages: dict = field(default_factory=dict)
    explanation: str = ""
