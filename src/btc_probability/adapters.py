"""Read-only HTTP and reference WebSocket adapters, independent of btc15 trading code."""

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .schema import Reference


class Clock:
    def time(self):
        return time.time()

    def monotonic(self):
        return time.monotonic()


class KalshiReader:
    def __init__(self, key_id="", key_path="", *, clock=None, transport=None):
        self.clock = clock or Clock()
        self.key_id = key_id
        self.key = (
            serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
            if key_path
            else None
        )
        self.base = "https://external-api.kalshi.com/trade-api/v2/"
        self.http = httpx.AsyncClient(base_url=self.base, timeout=15, transport=transport)
        self.next_read = 0
        self.lock = asyncio.Lock()

    @property
    def authenticated(self):
        return bool(self.key_id and self.key)

    def headers(self, path):
        if not self.authenticated:
            raise ValueError("REFERENCE_CREDENTIALS_MISSING")
        if path != "/trade-api/ws/v2" and not path.startswith("/trade-api/v2/cfbenchmarks/"):
            raise ValueError("Signing path is not an allowed read-only feed")
        stamp = str(int(self.clock.time() * 1000))
        signature = self.key.sign(
            (stamp + "GET" + path).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": stamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    async def get(self, path, params=None):
        # No generic method argument, write methods, portfolio paths, or arbitrary URLs.
        parts = path.split("/")
        allowed = (
            path == "markets"
            or (len(parts) == 2 and parts[0] in ("markets", "series"))
            or (len(parts) == 3 and parts[0] == "markets" and parts[2] == "orderbook")
            or path in ("cfbenchmarks/values", "cfbenchmarks/history/values")
        )
        if not allowed or any(x in path for x in ("..", "?", ":", "%", "\\")):
            raise ValueError("Endpoint outside read-only allowlist")
        for attempt in range(3):
            async with self.lock:
                await asyncio.sleep(max(0, self.next_read - self.clock.monotonic()))
                self.next_read = self.clock.monotonic() + (1 if path.startswith("cfbenchmarks/") else 0.2)
            headers = self.headers("/trade-api/v2/" + path) if path.startswith("cfbenchmarks/") else {}
            try:
                response = await self.http.get(path, params=params, headers=headers)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)

    async def rule_documents(self):
        """Fetch only the reviewed public PDFs; preserve bytes and actual retrieval provenance."""
        from .contract_profile import DOCUMENTS

        result = {}
        for name, expected in DOCUMENTS.items():
            response = await self.http.get(expected["url"])
            response.raise_for_status()
            result[name] = dict(
                url=str(response.url),
                status=response.status_code,
                received_time=self.clock.time(),
                sha256=hashlib.sha256(response.content).hexdigest(),
                content_base64=base64.b64encode(response.content).decode(),
            )
        return result

    async def history(self, end_iso):
        return await self.get(
            "cfbenchmarks/history/values", {"id": "BRTI", "timespan": "HOUR", "timestamp": end_iso}
        )

    async def reference_frames(self):
        uri = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
        async with websockets.connect(
            uri,
            additional_headers=self.headers(urlparse(uri).path),
            ping_interval=20,
            ping_timeout=20,
            max_queue=32,
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {"channels": ["cfbenchmarks_value"], "index_ids": ["BRTI"]},
                    }
                )
            )
            async for raw in ws:
                frame = json.loads(raw)
                received = self.clock.time()
                yield frame, received

    async def close(self):
        await self.http.aclose()


def normalize_reference(frame, received):
    msg = frame["msg"]
    raw = json.loads(msg["data"]) if isinstance(msg["data"], str) else msg["data"]
    if raw.get("type") != "value" or raw.get("id") != "BRTI" or msg.get("index_id") != "BRTI":
        raise ValueError("UNEXPECTED_REFERENCE_INDEX_OR_SCHEMA")
    return Reference(str(raw["value"]), float(raw["time"]) / 1000, received)


def history_references(payload, received):
    """Strict recognizable CF value objects only; unfamiliar historical envelopes stay raw.

    Recovery does not round subsecond timestamps into fictitious one-second slots.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("id") == "BRTI" and "time" in node and "value" in node:
                found.append(
                    Reference(str(node["value"]), float(node["time"]) / 1000, received, recovered=True)
                )
            else:
                for value in node.values():
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


class DeribitReader:
    def __init__(self, *, clock=None, transport=None):
        self.clock = clock or Clock()
        self.http = httpx.AsyncClient(
            base_url="https://www.deribit.com/api/v2/public/", timeout=15, transport=transport
        )
        self.next_read = 0

    async def get(self, endpoint, params):
        if endpoint not in ("get_instruments", "get_order_book"):
            raise ValueError("Endpoint outside public market-data allowlist")
        for attempt in range(3):
            await asyncio.sleep(max(0, self.next_read - self.clock.monotonic()))
            self.next_read = self.clock.monotonic() + 1
            try:
                r = await self.http.get(endpoint, params=params)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(2**attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise ValueError("DERIBIT_RPC_ERROR")
                return data
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)

    async def close(self):
        await self.http.aclose()
