from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from btc15.live_settlement_recovery import recover_live_settlements


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", [None, "not_final", "wrong_ticker", "wrong_close", "future"])
async def test_official_recovery_without_collector_history_is_validated_and_idempotent(
    store, raw, series, market, config, invalid
):
    now = market.close_time + 30
    final = dict(
        raw, status="finalized", result="yes", settlement_ts=datetime.fromtimestamp(now - 10, UTC).isoformat()
    )
    if invalid == "not_final":
        final["status"] = "closed"
    elif invalid == "wrong_ticker":
        final["ticker"] = "KXBTC15M-wrong"
    elif invalid == "future":
        final["settlement_ts"] = datetime.fromtimestamp(now + 10, UTC).isoformat()
    reads = []

    class Client:
        async def get(self, path):
            reads.append(path)
            return {"series": series} if path.startswith("series/") else {"market": final}

    @asynccontextmanager
    async def client():
        yield Client()

    manual = SimpleNamespace(
        client=client,
        rows=lambda: [
            dict(
                origin="bot",
                request=dict(ticker=market.ticker, action="buy"),
                exchange_order=dict(fill_count_fp="10.00"),
            )
        ],
    )
    controls = {
        market.ticker: dict(
            asset="BTC", close_time=market.close_time + (1 if invalid == "wrong_close" else 0)
        )
    }
    args = (manual, {"BTC": dict(config=config, run_id="new-run")}, {"BTC": store}, controls, now)
    result = await recover_live_settlements(*args)
    saved = store.list("settlement", "new-run", "PAPER")
    if invalid:
        assert result == [] and saved == []
    else:
        assert result == [market.ticker]
        assert saved[0]["body"]["result"] == "yes"
        assert saved[0]["timestamp"] == now - 10
        assert len(store.list("settlement_evidence")) == 1
        previous = len(reads)
        assert await recover_live_settlements(*args) == []
        assert len(reads) == previous
