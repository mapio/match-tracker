"""The event logs on disk: data/<team>/<match number>.json (docs/contract.md).

The process is the only writer, so a log is read once and then kept in memory;
every change goes to the file whole, through a rename, so a crash leaves either
the old log or the new one. Before the rename the change is added to the
match's journal (journal.py), which is what rebuilds a log that was lost.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import journal
from .rules import Event


@dataclass(frozen=True)
class Log:
    next_id: int = 1
    events: tuple[Event, ...] = ()


def format_time(t):
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event_to_json(event):
    out = {
        "id": event.id,
        "t": format_time(event.t),
        "set": event.set,
        "type": event.type,
    }
    if event.team is not None:
        out["team"] = event.team
    return out


def _event_from_json(raw):
    if "player" in raw:
        raise ValueError(f"event {raw.get('id')} has a 'player'; the app holds no name")
    return Event(
        id=raw["id"],
        t=datetime.fromisoformat(raw["t"]),
        set=raw["set"],
        type=raw["type"],
        team=raw.get("team"),
    )


class Store:
    def __init__(self, data_dir, now=lambda: datetime.now(UTC)):
        self._dir = Path(data_dir)
        self._now = now
        self._logs = {}

    def _journal(self, team_id, n, who, op, new, next_id):
        old = [event_to_json(e) for e in self.get(team_id, n).events]
        record = journal.match_record(self._now(), who, op, old, new, next_id)
        journal.append(journal.path_of(self._dir, team_id, n), record)

    def _path(self, team_id, n):
        return self._dir / team_id / f"{n}.json"

    def get(self, team_id, n):
        """The log of a match; an empty one if nothing was ever recorded."""
        key = (team_id, n)
        if key not in self._logs:
            path = self._path(team_id, n)
            if not path.exists():
                self._logs[key] = Log()
            else:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._logs[key] = Log(
                    raw["next_id"], tuple(_event_from_json(e) for e in raw["events"])
                )
        return self._logs[key]

    def reset(self, team_id, n, *, who="", op="reset"):
        """Forget everything recorded for a match."""
        self._journal(team_id, n, who, op, [], 1)
        self._path(team_id, n).unlink(missing_ok=True)
        self._logs[(team_id, n)] = Log()

    def put(self, team_id, n, log, *, who="", op=""):
        """Journal the change, write the whole log, then make it the one in memory.
        `who` is the role that acted and `op` what it did."""
        self._journal(
            team_id, n, who, op, [event_to_json(e) for e in log.events], log.next_id
        )
        path = self._path(team_id, n)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            {"next_id": log.next_id, "events": [event_to_json(e) for e in log.events]}
        )
        scratch = path.with_suffix(".tmp")
        with scratch.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        scratch.replace(path)
        self._logs[(team_id, n)] = log
