"""Local Linux process evidence for recovering a dead database writer."""

import os
import socket
from pathlib import Path


def process_start(pid):
    # comm may contain spaces or parentheses; starttime is field 22 after comm.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def current_process():
    try:
        return dict(
            host=socket.gethostname(),
            machine=Path("/etc/machine-id").read_text().strip(),
            boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            namespace=os.readlink("/proc/self/ns/pid"),
            pid=os.getpid(),
            start=process_start(os.getpid()),
        )
    except (OSError, IndexError):
        # Unsupported hosts retain manual, fail-closed lease recovery.
        return None


def owner_is_dead(saved, current):
    if not isinstance(saved, dict) or not current:
        return False
    if any(
        not isinstance(saved.get(k), str) or not saved[k]
        for k in ("host", "machine", "boot", "namespace", "start")
    ):
        return False
    if not saved["start"].isdigit():
        return False
    if type(saved.get("pid")) is not int or saved["pid"] <= 0:
        return False
    if any(saved[k] != current[k] for k in ("host", "machine")):
        return False
    if saved["boot"] != current["boot"]:
        return True
    if saved["namespace"] != current["namespace"]:
        return False
    try:
        return process_start(saved["pid"]) != saved["start"]
    except FileNotFoundError:
        return True
    except (OSError, IndexError):
        return False


class WriterOwnedError(RuntimeError):
    def __init__(self):
        super().__init__(
            "A writer owns this database. Its owner is active or cannot be verified dead; "
            "inspect the lease before manual recovery."
        )
