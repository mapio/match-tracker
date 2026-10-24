"""The journal: an append-only record of what staff, coach and admin did, to
rebuild data that a mistake or a bug lost.

The state on disk is rewritten whole at every change, and a deleted event leaves
the list, so without a record a wrong tap or a wrong line of code cannot be
undone. A line is added and flushed to the disk before the rename that replaces
the state, so whatever the state shows is in the journal:

- `data/<team>/<n>.journal.jsonl`: one line per change of the event log of match
  `n`, with what was added, removed and changed (whole events, and for a change
  the event before and after). `replay` rebuilds the log from it.
- `data/<team>/team.journal.jsonl`: one line per save of the team file (team
  form, season, creation), with the content the file had before.

The files are readable by their owner only. `match-tracker replay FILE` prints
the event log a match journal gives, as the store writes it.
"""

import argparse
import json
import os
from datetime import UTC
from pathlib import Path

TEAM = "team"


def stamp(t):
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def path_of(data_dir, team_id, name):
    """The journal of match `name` (its number) or of the team (`TEAM`)."""
    return Path(data_dir) / team_id / f"{name}.journal.jsonl"


def append(path, record):
    """Add one line and make sure it is on the disk before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    new = not path.exists()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    if new:  # the new name must survive a crash too
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def match_record(t, who, op, old, new, next_id):
    """What turns the events `old` into `new` (lists of event JSON), by id; the
    `next_id` is the counter the log has after the change."""
    before = {e["id"]: e for e in old}
    after = {e["id"]: e for e in new}
    return {
        "t": stamp(t),
        "who": who,
        "op": op,
        "next_id": next_id,
        "added": [e for e in new if e["id"] not in before],
        "removed": [e for e in old if e["id"] not in after],
        "changed": [
            {"before": before[i], "after": after[i]}
            for i in before
            if i in after and before[i] != after[i]
        ],
    }


def team_record(t, who, op, before):
    """A save of the team file: `before` is its JSON (None when it is created)."""
    return {"t": stamp(t), "who": who, "op": op, "before": before}


def replay(path, upto=None):
    """The event log of a match as the journal at `path` builds it, from the first
    line, or from the first `upto` lines: `{"next_id", "events"}`."""
    events, next_id = [], 1
    with Path(path).open(encoding="utf-8") as lines:
        for number, line in enumerate(lines):
            if upto is not None and number >= upto:
                break
            record = json.loads(line)
            gone = {e["id"] for e in record["removed"]}
            changed = {c["after"]["id"]: c["after"] for c in record["changed"]}
            events = [changed.get(e["id"], e) for e in events if e["id"] not in gone]
            events += record["added"]
            next_id = record["next_id"]
    return {"next_id": next_id, "events": events}


def show(argv):
    """`match-tracker replay FILE`: print the event log a match journal gives."""
    parser = argparse.ArgumentParser(
        prog="match-tracker replay", description=show.__doc__
    )
    parser.add_argument("journal", type=Path)
    parser.add_argument("--lines", type=int, help="stop after this many lines")
    args = parser.parse_args(argv)
    print(json.dumps(replay(args.journal, args.lines)))
