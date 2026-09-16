"""Read-only, fixed-trade sensitivity analysis; not a portfolio replay or live gate."""

import argparse
import itertools
import json
import sqlite3
from pathlib import Path

from btc15.config import Strategy
from btc15.domain import D, parse_market
from btc15.strategies.settlement_edge.economics import entry_economics

EXCLUDED = "3f421453-de01-4898-bfce-285c5f052998"  # User-excluded trade 19.


def passes(report, proxy_min, profit_min):
    if report.get("status") != "AVAILABLE":
        return False
    sale = report.get("target_sale", {})
    if sale.get("status") != "AVAILABLE":
        return False
    return D(sale["probability_weighted_proxy_per_contract"]) >= D(proxy_min) and D(
        sale["net_per_contract"]
    ) >= D(profit_min)


def summarize(rows, proxy_min=None, profit_min=None):
    kept = (
        rows
        if proxy_min is None
        else [
            r
            for r in rows
            if all(passes(r[stage], proxy_min, profit_min) for stage in ("evaluation", "order_limit"))
        ]
    )
    return dict(
        kept=[r["number"] for r in kept],
        rejected=[r["number"] for r in rows if r not in kept],
        trades=len(kept),
        wins=sum(r["net_pnl"] > 0 for r in kept),
        losses=sum(r["net_pnl"] < 0 for r in kept),
        net_pnl=round(sum(r["net_pnl"] for r in kept), 6),
        fees=round(sum(r["fees"] for r in kept), 6),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/btc15.db")
    parser.add_argument("--output", default="data/runtime/both-leg-filter-test.json")
    args = parser.parse_args()
    c = sqlite3.connect(Path(args.database).resolve().as_uri() + "?mode=ro", uri=True)
    c.execute("BEGIN")  # Freeze all reads to one snapshot while the paper bot continues.
    run = "settlement-convergence-v7"

    def records(kind, op=None, market=None):
        sql = "select timestamp,market,opportunity_id,body from records where run_id=? and kind=?"
        params = [run, kind]
        for name, value in [("opportunity_id", op), ("market", market)]:
            if value is not None:
                sql += " and " + name + "=?"
                params.append(value)
        return [
            dict(timestamp=t, market=m, op=o, body=json.loads(b))
            for t, m, o, b in c.execute(sql + " order by timestamp,id", params)
        ]

    trades = sorted(records("trade_result"), key=lambda r: r["body"]["opened"])
    rows = []
    for number, trade in enumerate(trades, 1):
        if trade["op"] == EXCLUDED:
            continue
        op = records("opportunity", op=trade["op"])[0]["body"]
        order = next(
            r["body"] for r in records("order", op=trade["op"]) if r["body"].get("status") == "submitted"
        )
        cfg = Strategy(**op["config"])
        # Frozen contract identity from the evaluation; market record supplies tick bands.
        metadata = records("market", market=trade["market"])[0]["body"]
        market = parse_market(metadata["raw"], metadata["series"])
        evaluation = entry_economics(
            market,
            None,
            op["side"],
            op["expected_fill_price"],
            order["quantity"],
            op["conservative_probability"],
            cfg,
            op["timestamp"],
            stage="evaluation",
            entry_slippage=op["expected_slippage"],
        )
        limit = entry_economics(
            market,
            None,
            order["side"],
            order["limit"],
            order["quantity"],
            order["entry_checks"]["conservative_probability"],
            cfg,
            order["created"],
            stage="order_limit",
            entry_slippage=0,
        )
        # An independent Decimal reconstruction verifies probability-weighted proceeds.
        for r in (evaluation, limit):
            assert r["status"] == "AVAILABLE" and r["target_sale"]["status"] == "AVAILABLE"
            q, p = D(r["quantity"]), D(r["probability"])
            debit = q * D(r["entry_price"]) + D(r["entry_fee"]) + D(r["entry_slippage_total"])
            sale = r["target_sale"]
            proceeds = q * D(sale["assumed_sale_price"]) - D(sale["exit_fee"])
            assert abs(D(sale["net_per_contract"]) - (proceeds - debit) / q) < D("1e-12")
            assert abs(D(sale["probability_weighted_proxy_per_contract"]) - (p * proceeds - debit) / q) < D(
                "1e-12"
            )
        rows.append(
            dict(
                number=number,
                trade_id=trade["op"],
                market=trade["market"],
                opened=trade["body"]["opened"],
                completed=trade["timestamp"],
                net_pnl=trade["body"]["net_pnl"],
                fees=trade["body"]["fees"],
                evaluation=evaluation,
                order_limit=limit,
            )
        )
    c.close()
    groups = {
        "original_review_excluding_19": [r for r in rows if r["number"] <= 20],
        "corrected_execution_excluding_19": [r for r in rows if 13 <= r["number"] <= 20],
        "recent_16_to_20_excluding_19": [r for r in rows if 16 <= r["number"] <= 20],
        "additional_after_review": [r for r in rows if r["number"] > 20],
    }
    grid = []
    # Small sensitivity grid, not a search for an optimized historical winner.
    for proxy, profit in itertools.product([0, 0.005, 0.01], [0, 0.02, 0.05, 0.10]):
        grid.append(
            dict(
                proxy_min=proxy,
                profit_min=profit,
                groups={k: summarize(v, proxy, profit) for k, v in groups.items()},
            )
        )
    out = dict(
        method="Fixed observed trades filtered at both evaluation and submitted limit; no replacement entries, portfolio feedback or exit replay.",
        exclusion=EXCLUDED,
        baseline={k: summarize(v) for k, v in groups.items()},
        candidate=dict(proxy_min=0.01, profit_min=0.05, units="dollars per contract"),
        grid=grid,
        rows=rows,
    )
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    print("BASELINE", json.dumps(out["baseline"]))
    for g in grid:
        print(g["proxy_min"], g["profit_min"], json.dumps(g["groups"]))


if __name__ == "__main__":
    main()
