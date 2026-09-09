import pytest

from btc15.demo import generate
from btc15.runner import backtest
from btc15.storage import CompactRecorder, read_events


def test_compact_tape_preserves_inputs_and_paper_fill_replay(store, config, tmp_path):
    source = generate(tmp_path / "source.jsonl")
    rows = list(read_events(source))
    recorder = CompactRecorder(tmp_path / "compact")
    for offset in range(0, len(rows), 256):
        recorder.append_rows(rows[offset : offset + 256])
    recorder.close()
    assert list(read_events(recorder.path)) == rows
    assert recorder.path.stat().st_size < source.stat().st_size
    original = backtest(source, config, store)
    compressed = backtest(recorder.path, config, store)
    for kind, fields in (
        ("fill", ("action", "quantity", "price", "fee")),
        ("trade_result", ("net_pnl",)),
        ("opportunity", ("decision", "probability", "conservative_probability", "net_ev", "reasons")),
    ):

        def values(run):
            return [[r["body"].get(f) for f in fields] for r in store.list(kind=kind, run_id=run, limit=None)]

        assert values(original)
        assert values(original) == values(compressed)


def test_compact_tape_detects_torn_batch(tmp_path):
    recorder = CompactRecorder(tmp_path)
    for i in range(2):
        recorder.append_rows(
            [dict(id=str(i), received=i, monotonic_ns=i, connection_id="c", payload={"type": "disconnect"})]
        )
    recorder.close()
    recorder.path.write_bytes(recorder.path.read_bytes()[:-5])
    with pytest.raises(EOFError):
        list(read_events(recorder.path))
