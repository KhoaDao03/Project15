import asyncio
import base64

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from btc15.api import KalshiClient, subscriptions
from btc15.config import Settings


def test_signature_excludes_query_and_uses_digest_salt(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "key.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    client = KalshiClient(Settings(api_key_id="test-key", private_key_path=str(path)))
    h = client.headers("GET", "/trade-api/v2/markets?limit=2")
    key.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
        (h["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/v2/markets").encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    asyncio.run(client.close())


def test_discovery_pagination_shard_and_next_market(raw, series):
    visited = []
    upcoming = {
        **raw,
        "ticker": "KXBTC15M-NEXT-00",
        "status": "initialized",
        "close_time": "2026-09-08T22:15:00Z",
        "floor_strike": None,
    }
    later = {**upcoming, "ticker": "KXBTC15M-LATER-00", "close_time": "2026-09-08T22:30:00Z"}

    def handler(request):
        visited.append(request)
        if request.url.path.endswith("/series/KXBTC15M"):
            return httpx.Response(200, json={"series": series})
        assert request.url.params["exchange_index"] == "2"
        if request.url.params["status"] == "open":
            return httpx.Response(200, json={"markets": [raw], "cursor": ""})
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"markets": [later], "cursor": "next"})
        return httpx.Response(200, json={"markets": [upcoming], "cursor": ""})

    async def run():
        client = KalshiClient(Settings())
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            base_url=Settings.rest_url + "/", transport=httpx.MockTransport(handler)
        )
        s, markets = await client.discover()
        await client.close()
        assert [m["ticker"] for m in markets] == [raw["ticker"], upcoming["ticker"]]

    asyncio.run(run())
    assert len(visited) == 4
    subs = subscriptions(["KXBTC15M-TEST-00"])
    assert subs[2]["params"]["use_yes_price"] is True
    assert subs[0]["params"]["index_ids"] == ["BRTI"]
    assert "market_tickers" not in subs[1]["params"]
