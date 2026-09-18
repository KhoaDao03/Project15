"""Fixed-point binary order book. A gap invalidates all comparisons until a snapshot."""

from .schema import decimal


class Book:
    def __init__(self):
        self.levels = {"yes": {}, "no": {}}
        self.valid = False
        self.sequence = None
        self.sid = None
        self.received = None
        self.source_time = None
        self.reason = "NO_BOOK"

    def invalidate(self, reason="RECONNECT"):
        self.valid = False
        self.reason = reason

    def apply(self, event, received):
        kind, msg = event["type"], event.get("msg", {})
        seq = event.get("seq")
        if kind in ("rest_snapshot", "orderbook_snapshot"):
            payload = msg.get("orderbook_fp", msg)
            levels = {}
            for side in ("yes", "no"):
                values = payload.get(side + "_dollars_fp", payload.get(side + "_dollars", [])) or []
                levels[side] = {}
                for price, size in values:
                    price, size = decimal(price), decimal(size)
                    if not 0 <= price <= 1 or size < 0:
                        self.invalidate("BAD_BOOK")
                        return
                    if size:
                        levels[side][price] = size
            self.levels = levels
            self.sid, self.sequence = event.get("sid"), seq
            self.valid, self.reason = True, ""
        elif kind == "orderbook_delta":
            if (
                not self.valid
                or self.sequence is None
                or seq != self.sequence + 1
                or event.get("sid") != self.sid
            ):
                self.invalidate("BOOK_SEQUENCE_GAP")
                return
            side = msg["side"]
            if side not in self.levels:
                self.invalidate("BAD_BOOK_SIDE")
                return
            price = decimal(msg["price_dollars"])
            size = self.levels[side].get(price, decimal(0)) + decimal(msg["delta_fp"])
            if not 0 <= price <= 1 or size < 0:
                self.invalidate("NEGATIVE_BOOK_SIZE")
                return
            if size:
                self.levels[side][price] = size
            else:
                self.levels[side].pop(price, None)
            self.sequence = seq
        else:
            raise ValueError("Unknown book event")
        self.received = received
        self.source_time = msg["ts_ms"] / 1000 if msg.get("ts_ms") is not None else None
        if self.levels["yes"] and self.levels["no"] and max(self.levels["yes"]) + max(self.levels["no"]) > 1:
            self.invalidate("CROSSED_BOOK")

    def quotes(self, now, max_age):
        age = None if self.received is None else now - self.received
        source_age = None if self.source_time is None else now - self.source_time
        valid = (
            self.valid
            and age is not None
            and 0 <= age <= max_age
            and (source_age is None or 0 <= source_age <= max_age)
        )
        out = dict(
            yes_bid=None,
            yes_ask=None,
            no_bid=None,
            no_ask=None,
            midpoint=None,
            last=None,
            yes_bid_size=None,
            yes_ask_size=None,
            no_bid_size=None,
            no_ask_size=None,
            spread=None,
            age=age,
            source_age=source_age,
            valid=valid,
            reason=self.reason if not self.valid else ("" if valid else "STALE_BOOK"),
            price_selection="midpoint; not executable",
            source_time=self.source_time,
            age_basis="source and receipt"
            if self.source_time is not None
            else "snapshot receipt; source time not supplied",
            received_time=self.received,
        )
        if not valid:
            return out
        for side, other in [("yes", "no"), ("no", "yes")]:
            if self.levels[side]:
                bid = max(self.levels[side])
                out[side + "_bid"] = str(bid)
                out[side + "_bid_size"] = str(self.levels[side][bid])
            if self.levels[other]:
                bid = max(self.levels[other])
                out[side + "_ask"] = str(1 - bid)
                out[side + "_ask_size"] = str(self.levels[other][bid])
        if out["yes_bid"] is not None and out["yes_ask"] is not None:
            bid, ask = decimal(out["yes_bid"]), decimal(out["yes_ask"])
            out.update(midpoint=str((bid + ask) / 2), spread=str(ask - bid))
        return out
