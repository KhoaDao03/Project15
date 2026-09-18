"""Verified identities for supported 15-minute contracts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    symbol: str
    index: str
    rule_index: str
    round_digits: int

    @property
    def series(self):
        return f"KX{self.symbol}15M"

    @property
    def commodity(self):
        return self.symbol in ("GOLD", "SILVER", "WTI")

    @property
    def reference_source(self):
        return "Pyth" if self.commodity else "CF Benchmarks"


ASSETS = {
    "BTC": Asset("BTC", "BRTI", "BRTI", 2),
    "ETH": Asset("ETH", "ETHUSD_RTI", "ETHUSDRTI", 2),
    "SOL": Asset("SOL", "SOLUSD_RTI", "SOLUSDRTI", 4),
    "XRP": Asset("XRP", "XRPUSD_RTI", "XRPUSDRTI", 4),
    "GOLD": Asset("GOLD", "Metal.Index.1OZGOLD/USD", "GOLD", 2),
    "SILVER": Asset("SILVER", "Metal.Index.SILVER/USD", "SILVER", 3),
    "WTI": Asset("WTI", "Commodities.Index.PYTHOIL/USD", "PYTHOIL", 2),
}


def asset_spec(symbol):
    if not isinstance(symbol, str) or symbol not in ASSETS:
        raise ValueError("Asset must be " + ", ".join(ASSETS))
    return ASSETS[symbol]
