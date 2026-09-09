import json
import os
import uuid
from pathlib import Path

import pytest

from btc15.config import Strategy
from btc15.domain import Book, parse_market
from btc15.storage import Store


@pytest.fixture
def raw():
    return json.loads((Path(__file__).parent / "fixtures/markets-20260908.json").read_text())["markets"][0]


@pytest.fixture
def series():
    return json.loads((Path(__file__).parent / "fixtures/series-20260908.json").read_text())["series"]


@pytest.fixture
def market(raw, series):
    return parse_market(raw, series)


@pytest.fixture
def config():
    return Strategy(paths=500)


@pytest.fixture
def store(tmp_path):
    url = os.getenv("BTC15_TEST_DATABASE_URL")
    if url:
        from sqlalchemy import create_engine

        schema = "test_" + uuid.uuid4().hex
        admin = create_engine(url)
        with admin.begin() as c:
            c.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
        separator = "&" if "?" in url else "?"
        s = Store(url + separator + "options=-csearch_path%3D" + schema)
        try:
            yield s
        finally:
            s.engine.dispose()
            with admin.begin() as c:
                c.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
            admin.dispose()
    else:
        s = Store("sqlite:///" + str(tmp_path / "test.db"))
        yield s
        s.engine.dispose()


@pytest.fixture
def now(market):
    return market.close_time - 300


@pytest.fixture
def book(now):
    b = Book()
    b.snapshot(dict(yes_dollars_fp=[[".88", "2.50"]], no_dollars_fp=[[".90", "20.75"]]), now)
    return b
