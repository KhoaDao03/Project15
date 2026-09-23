"""Read finalized research segments in file order; preserve each session's capture order."""

import gzip
import json


def records(root):
    result = []
    for path in sorted(root.rglob("events-*.jsonl.gz")):
        with gzip.open(path, "rt") as stream:
            result.extend(json.loads(line) for line in stream)
    return result
