"""Read-only comparison report; primary trade history is never modified."""

import argparse
import json
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database", default="data/btc15.db")
parser.add_argument("--run-id", default="settlement-convergence-v7")
args = parser.parse_args()
with sqlite3.connect(Path(args.database).resolve().as_uri() + "?mode=ro", uri=True) as connection:
    rows = [
        (market, json.loads(body))
        for market, body in connection.execute(
            "select market,body from records where run_id=? and kind='stop_shadow_comparison' order by timestamp",
            (args.run_id,),
        )
    ]
print("Fixed actual purchases; shadow outcomes do not change primary trading.")
print("Market                         Primary   Shadow    Change  Input interruption")
for market, r in rows:
    print(
        f"{market:30} {r['primary_net_pnl']:8.4f} {r['shadow_net_pnl']:8.4f} {r['difference']:8.4f}  {r.get('input_interruption', False)}"
    )
print(
    json.dumps(
        dict(
            completed_pairs=len(rows),
            primary_net=sum(r["primary_net_pnl"] for _, r in rows),
            shadow_net=sum(r["shadow_net_pnl"] for _, r in rows),
            improved=sum(r["difference"] > 0 for _, r in rows),
            worsened=sum(r["difference"] < 0 for _, r in rows),
            worst_primary=min((r["primary_net_pnl"] for _, r in rows), default=None),
            worst_shadow=min((r["shadow_net_pnl"] for _, r in rows), default=None),
        ),
        indent=2,
    )
)
