"""Verified identities for the four supported 15-minute crypto contracts."""

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


ASSETS = {
    "BTC": Asset("BTC", "BRTI", "BRTI", 2),
    "ETH": Asset("ETH", "ETHUSD_RTI", "ETHUSDRTI", 2),
    "SOL": Asset("SOL", "SOLUSD_RTI", "SOLUSDRTI", 4),
    "XRP": Asset("XRP", "XRPUSD_RTI", "XRPUSDRTI", 4),
}


def asset_spec(symbol):
    if not isinstance(symbol, str) or symbol not in ASSETS:
        raise ValueError("Asset must be BTC, ETH, SOL or XRP")
    return ASSETS[symbol]
