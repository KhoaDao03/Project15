"""Best-effort local wakeups; the committed ledger remains authoritative."""

import asyncio
import hashlib
import os
import socket
from pathlib import Path


def notification_path(database):
    digest = hashlib.sha256(str(Path(database).resolve()).encode()).hexdigest()[:24]
    return Path("/tmp") / f"btc15-decisions-{os.getuid()}" / (digest + ".sock")


def notify_decision(database):
    if not database or database == ":memory:":
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.setblocking(False)
            sender.sendto(b"decision", str(notification_path(database)))
    except OSError:
        pass  # Missing listener or full queue must never delay collection.


class DecisionListener:
    def __init__(self, databases, wakeup):
        self.databases, self.wakeup = databases, wakeup
        self.sockets = []

    def start(self):
        loop = asyncio.get_running_loop()
        for database in self.databases:
            path = notification_path(database)
            receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                path.parent.mkdir(mode=0o700, exist_ok=True)
                stat = path.parent.stat()
                if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                    raise OSError("Notification directory must be private")
                path.unlink(missing_ok=True)  # Caller holds the live worker process lock.
                receiver.bind(str(path))
                receiver.setblocking(False)
                loop.add_reader(receiver.fileno(), self.receive, receiver)
                self.sockets.append((receiver, path))
            except OSError:
                receiver.close()  # Polling remains available if local IPC is unavailable.

    def receive(self, receiver):
        # Bound each callback so a burst cannot monopolize the event loop.
        for _ in range(64):
            try:
                receiver.recv(64)
            except BlockingIOError:
                break
            self.wakeup.set()

    def close(self):
        loop = asyncio.get_running_loop()
        for receiver, path in self.sockets:
            loop.remove_reader(receiver.fileno())
            receiver.close()
            path.unlink(missing_ok=True)
        self.sockets.clear()
