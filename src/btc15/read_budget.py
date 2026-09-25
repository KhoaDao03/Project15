"""Host-shared Kalshi read tokens; short file locks never span network calls.

The deployed account reported 200 tokens/s and 600 capacity on 2026-09-25.
All credentialed clients under the same OS user/API origin share this budget,
including unauthenticated metadata reads made by those clients. Other hosts and
external clients are not observable; 429s discard credit and impose shared backoff.
"""

import asyncio
import fcntl
import hashlib
import json
import math
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

READ_RATE = 200.0
READ_CAPACITY = 600.0
STOP_RESERVE = 30.0  # Three ordinary hard-stop preflight reads.
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def shared_budget_path(rest_url):
    url = urlsplit(rest_url)
    origin = f"{url.scheme.lower()}://{url.netloc.lower()}"
    digest = hashlib.sha256(origin.encode()).hexdigest()[:24]
    return Path("/tmp") / f"btc15-read-budget-{os.getuid()}" / (digest + ".json")


def read_cost(path):
    parts = path.split("?", 1)[0].strip("/").split("/")
    if parts[0] == "cfbenchmarks":
        return 50
    if len(parts) == 3 and parts[:2] == ["portfolio", "orders"]:
        return 2
    return 10  # Conservative for other discounted endpoints.


class ReadBudget:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.state = self.empty(time.monotonic())

    @staticmethod
    def empty(now):
        # Never grant fresh burst credit merely because a process/file restarted.
        return dict(boot=BOOT_ID, at=now, tokens=0.0, blocked_until=0.0)

    @contextmanager
    def locked_state(self):
        if self.path is None:
            yield self.state
            return
        directory = self.path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Read budget directory must be private and owned by the service user")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise RuntimeError("Read budget file must be private and owned by the service user")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield None
                return
            now = time.monotonic()
            try:
                state = json.loads(os.read(fd, 4096))
                valid = (
                    state["boot"] == BOOT_ID
                    and all(
                        type(state[k]) in (int, float) and math.isfinite(state[k])
                        for k in ("at", "tokens", "blocked_until")
                    )
                    and 0 <= state["at"] <= now
                    and 0 <= state["tokens"] <= READ_CAPACITY
                )
                if not valid:
                    state = self.empty(now)
            except (ValueError, KeyError, TypeError):
                state = self.empty(now)
            yield state
            raw = json.dumps(state).encode()
            os.lseek(fd, 0, os.SEEK_SET)
            # Small ephemeral state; a crash/truncated write resets to zero credit.
            if os.write(fd, raw) != len(raw):
                raise OSError("Incomplete read budget write")
            os.ftruncate(fd, len(raw))
        finally:
            os.close(fd)

    def delay(self, cost, *, stop=False):
        with self.locked_state() as state:
            if state is None:
                return 0.005  # Another process owns the short accounting lock.
            now = time.monotonic()
            state["tokens"] = min(READ_CAPACITY, state["tokens"] + max(0, now - state["at"]) * READ_RATE)
            state["at"] = now
            required = cost + (0 if stop else STOP_RESERVE)
            delay = max(0.0, state["blocked_until"] - now, (required - state["tokens"]) / READ_RATE)
            if delay == 0:
                state["tokens"] -= cost
            return delay

    async def acquire(self, cost, *, stop=False):
        if not 0 < cost <= READ_CAPACITY - STOP_RESERVE:
            raise ValueError("Unsupported read cost")
        while delay := self.delay(cost, stop=stop):
            await asyncio.sleep(delay)

    async def penalize(self, delay):
        while True:
            with self.locked_state() as state:
                if state is not None:
                    now = time.monotonic()
                    state.update(tokens=0.0, at=now, blocked_until=max(state["blocked_until"], now + delay))
                    return
            await asyncio.sleep(0.005)
