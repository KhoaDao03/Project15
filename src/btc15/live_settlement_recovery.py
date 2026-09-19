"""Recover official live outcomes independently of paper-worker health and run cutovers."""

import logging
from decimal import Decimal

from .domain import parse_market, timestamp

log = logging.getLogger(__name__)


async def recover_live_settlements(manual, members, stores, controls, now):
    bought = {
        row["request"]["ticker"]
        for row in manual.rows()
        if row.get("origin") == "bot"
        and row["request"]["action"] == "buy"
        and Decimal((row.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0
    }
    repaired = []
    for asset, member in members.items():
        store, run = stores[asset], member["run_id"]
        settled = {r["market"] for r in store.list("settlement", run, "PAPER", limit=None)}
        for ticker in sorted(bought - settled):
            control = controls.get(ticker)
            if not control or control["asset"] != asset or control["close_time"] > now:
                continue
            try:
                async with manual.client() as client:
                    raw = (await client.get("markets/" + ticker))["market"]
                    if raw.get("status") != "finalized" or raw.get("result") not in ("yes", "no"):
                        continue
                    series = (await client.get("series/" + member["config"].asset_spec.series))["series"]
                market = parse_market(raw, series)
                at = timestamp(raw["settlement_ts"])
                if (
                    market.ticker != ticker
                    or market.close_time != control["close_time"]
                    or not market.close_time <= at <= now
                ):
                    raise ValueError("Final market identity or settlement time mismatch")
                with store.transaction():
                    # The collector may have recorded it while the REST request was in flight.
                    if store.list("settlement", run, "PAPER", market=ticker, limit=1):
                        continue
                    evidence_id = store.add(
                        "settlement_evidence",
                        dict(
                            evidence=dict(source="kalshi_rest", market=raw, series=series),
                            source="live_executor_recovery",
                        ),
                        run,
                        "PAPER",
                        now,
                        ticker,
                    )
                    store.add(
                        "settlement",
                        dict(result=raw["result"], evidence_id=evidence_id, source="live_executor_recovery"),
                        run,
                        "PAPER",
                        at,
                        ticker,
                    )
                repaired.append(ticker)
            except Exception:
                log.exception("Live settlement recovery unavailable for %s", ticker)
    return repaired
