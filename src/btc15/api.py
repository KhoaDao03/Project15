"""Read-only Kalshi REST, signed WebSocket, and public market discovery."""

import asyncio
import base64
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiClient:
    def __init__(self, settings):
        self.settings = settings
        self.http = httpx.AsyncClient(base_url=settings.rest_url + "/", timeout=20)
        self.key = None
        if settings.private_key_path:
            self.key = serialization.load_pem_private_key(
                Path(settings.private_key_path).read_bytes(), password=None
            )
        self.last_clock_skew = None
        self._read_lock = asyncio.Lock()
        self._next_read = 0.0

    def headers(self, method, path):
        if self.key is None or not self.settings.api_key_id:
            raise RuntimeError("Authenticated feed requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH")
        timestamp = str(int(time.time() * 1000))
        payload = (timestamp + method.upper() + path.split("?")[0]).encode()
        signature = self.key.sign(
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.settings.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    async def get(self, path, params=None, authenticated=False):
        for attempt in range(4):
            # Budget our client at 50 read tokens/second, including retries.
            # Ordinary reads cost 10; CF passthrough reads cost 50.
            async with self._read_lock:
                await asyncio.sleep(max(0, self._next_read - time.monotonic()))
                self._next_read = time.monotonic() + (
                    1.0 if path.lstrip("/").startswith("cfbenchmarks/") else 0.2
                )
            signed_path = urlparse(self.settings.rest_url).path + "/" + path.lstrip("/")
            headers = self.headers("GET", signed_path) if authenticated else {}
            start = time.time()
            response = await self.http.get(path.lstrip("/"), params=params, headers=headers)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    try:
                        delay = float(response.headers.get("Retry-After", 2**attempt))
                    except ValueError:
                        delay = 2**attempt
                    await asyncio.sleep(min(10, max(0.1, delay)))
                    continue
            response.raise_for_status()
            if response.headers.get("Date"):
                from email.utils import parsedate_to_datetime

                self.last_clock_skew = (
                    parsedate_to_datetime(response.headers["Date"]).timestamp() - (start + time.time()) / 2
                )
            return response.json()
        raise RuntimeError("GET retries exhausted")

    async def pages(self, path, key, params=None, authenticated=False):
        params = dict(params or {})
        seen = set()
        while True:
            response = await self.get(path, params, authenticated)
            for row in response[key]:
                yield row
            cursor = response.get("cursor")
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("Repeated API cursor")
            seen.add(cursor)
            params["cursor"] = cursor

    async def discover(self):
        series = (await self.get("series/KXBTC15M"))["series"]
        index = series.get("exchange_index")
        if index is None:
            raise ValueError("Series exchange index missing")
        markets = []
        for status in ("open", "unopened"):
            async for raw in self.pages(
                "markets",
                "markets",
                dict(series_ticker="KXBTC15M", status=status, limit=100, exchange_index=index),
            ):
                if raw is not None:
                    markets.append(raw)
        # Preserve invalid metadata for research; runner performs strict validation.
        ordered = sorted(markets, key=lambda m: m["close_time"])
        active = [m for m in ordered if m.get("status") == "active"]
        upcoming = [m for m in ordered if m.get("status") != "active"]
        return series, active + upcoming[:1]

    async def reconciliation(self, exchange_index):
        result = {}
        for path, key in [
            ("portfolio/orders", "orders"),
            ("portfolio/fills", "fills"),
            ("portfolio/positions", "market_positions"),
        ]:
            result[key] = [
                r
                async for r in self.pages(path, key, {"exchange_index": exchange_index}, True)
                if r is not None
            ]
        return result

    async def close(self):
        await self.http.aclose()


def subscriptions(tickers):
    return [
        dict(id=1, cmd="subscribe", params=dict(channels=["cfbenchmarks_value"], index_ids=["BRTI"])),
        dict(id=2, cmd="subscribe", params=dict(channels=["cfbenchmarks_value_5hz"], index_ids=["BRTI"])),
        dict(
            id=3,
            cmd="subscribe",
            params=dict(channels=["orderbook_delta"], market_tickers=tickers, use_yes_price=True),
        ),
        dict(id=4, cmd="subscribe", params=dict(channels=["trade", "ticker"], market_tickers=tickers)),
        dict(id=5, cmd="subscribe", params=dict(channels=["market_lifecycle_v2"])),
    ]
