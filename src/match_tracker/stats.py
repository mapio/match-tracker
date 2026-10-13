"""Anonymous statistics: how many pages were opened and by how many visitors, and
what the staff and the coach changed. tests/test_stats.py checks that no address
or link is written.

A visitor is counted without a cookie: an HMAC of address and User-Agent under a
random secret made for each day and kept in memory only, so the same visitor is
recognised within a day and never across days, and nothing that could identify
one is ever written. What reaches `logs/stats-YYYY-MM.jsonl` is counters per
day, team, match and kind of page, and one line for each edit.
"""

import hashlib
import hmac
import json
import os
import secrets
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DAY_ZONE = ZoneInfo("Europe/Rome")  # where the days of the service begin and end
KEEP_MONTHS = 24
FLUSH_EVERY = 60  # seconds; a crash loses at most this much of the page counts


def month_of(day):
    return day[:7]


def oldest_kept(today):
    """The first month (YYYY-MM) whose statistics are still kept."""
    index = today.year * 12 + today.month - 1 - KEEP_MONTHS
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


class Stats:
    """Counters in memory, written cumulatively per run of the server: a restart
    in the day neither loses nor repeats what was counted. With no directory it
    counts nothing (the tests, and the commands that do not serve)."""

    def __init__(self, directory, now):
        self.directory = Path(directory) if directory else None
        self.now = now
        self.run = secrets.token_hex(4)
        self.day = None
        self.secret = b""
        self.hits = {}  # (team, match, page) -> count, for the day in hand
        self.visitors = {}  # (team, match, role) -> the daily identifiers seen
        self.dirty = False
        self.flushed_at = None
        if self.directory is not None:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._purge()

    def _today(self):
        return self.now().astimezone(DAY_ZONE).date()

    def _purge(self):
        first = oldest_kept(self._today())
        for old in self.directory.glob("stats-*.jsonl"):
            if old.stem.removeprefix("stats-") < first:
                old.unlink()

    def _roll(self):
        """Start the day's counters and secret when the day has changed."""
        day = self._today().isoformat()
        if day != self.day:
            self.flush()
            self.day = day
            self.secret = secrets.token_bytes(32)  # the previous day's is dropped
            self.hits, self.visitors = {}, {}

    def _append(self, day, lines):
        path = self.directory / f"stats-{month_of(day)}.jsonl"
        text = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as out:
            out.write(text)

    def visit(self, address, agent, role, team, match, kind):
        """Count one successful load of a page, by one anonymous visitor."""
        if self.directory is None:
            return
        self._roll()
        who = hmac.new(
            self.secret, f"{address}\0{agent}".encode(), hashlib.sha256
        ).digest()
        key = (team, match, f"{role}:{kind}")
        self.hits[key] = self.hits.get(key, 0) + 1
        self.visitors.setdefault((team, match, role), set()).add(who)
        self.dirty = True
        at = self.now().timestamp()
        if self.flushed_at is None or at - self.flushed_at >= FLUSH_EVERY:
            self.flush()

    def edit(self, role, team, match, kind):
        """Note one change: the kind of person (staff, coach, admin) and of change."""
        if self.directory is None:
            return
        self._roll()
        at = self.now().astimezone(DAY_ZONE).isoformat(timespec="seconds")
        line = {"t": "edit", "at": at, "role": role, "team": team, "match": match}
        self._append(self.day, [{**line, "kind": kind}])

    def flush(self):
        if self.directory is None or not self.dirty:
            return
        lines = [
            {"t": "hits", "run": self.run, "day": self.day, "team": t, "match": m}
            | {"page": page, "hits": count}
            for (t, m, page), count in self.hits.items()
        ]
        lines += [
            {"t": "visitors", "run": self.run, "day": self.day, "team": t}
            | {"match": m, "role": role, "visitors": len(seen)}
            for (t, m, role), seen in self.visitors.items()
        ]
        self._append(self.day, lines)
        self.dirty = False
        self.flushed_at = self.now().timestamp()


def read(directory, since):
    """The counters of the days from `since` (a date) on, runs summed:
    `(visitors, hits, edits)` as dicts keyed by tuples, edits counted by kind."""
    last = {}  # the latest cumulative line of each run and key
    edits = {}
    first = since.isoformat()
    for path in sorted(Path(directory).glob("stats-*.jsonl")):
        for text in path.read_text(encoding="utf-8").splitlines():
            line = json.loads(text)
            if line["t"] == "edit":
                if line["at"][:10] >= first:
                    key = (line["at"][:10], line["team"], line["role"], line["kind"])
                    edits[key] = edits.get(key, 0) + 1
            elif line["day"] >= first:
                what = (line.get("match"), line.get("page"), line.get("role"))
                last[(line["t"], line["run"], line["day"], line["team"], *what)] = line
    visitors, hits = {}, {}
    for line in last.values():
        if line["t"] == "visitors":
            key = (line["day"], line["team"], line["match"], line["role"])
            visitors[key] = visitors.get(key, 0) + line["visitors"]
        else:
            key = (line["day"], line["team"], line["match"], line["page"])
            hits[key] = hits.get(key, 0) + line["hits"]
    return visitors, hits, edits


def report(directory, today, days):
    """The text `match-tracker stats` prints for the last `days` days."""
    visitors, hits, edits = read(directory, today - timedelta(days=days - 1))
    out = [f"Statistics of the last {days} days (a visitor is counted once a day).", ""]
    out.append("Per day, team, match and kind of visitor:")
    per_role = {}
    for (day, team, match, page), count in hits.items():
        key = (day, team, match, page.split(":")[0])
        per_role[key] = per_role.get(key, 0) + count
    head = ("day", "team", "match", "who", "visitors", "pages")
    out.append("  {:10}  {:12}  {:>5}  {:7}  {:>8}  {:>6}".format(*head))
    for key in sorted(per_role.keys() | visitors.keys(), key=str):
        day, team, match, role = key
        row = (day, team or "-", "-" if match is None else match, role)
        row += (visitors.get(key, 0), per_role.get(key, 0))
        out.append("  {:10}  {:12}  {:>5}  {:7}  {:>8}  {:>6}".format(*row))
    out += ["", "Pages opened, whole period:"]
    per_page = {}
    for (day, team, match, page), count in hits.items():
        key = (team or "-", "-" if match is None else match, page)
        per_page[key] = per_page.get(key, 0) + count
    out.append(f"  {'team':12}  {'match':>5}  {'page':16}  {'opened':>6}")
    for (team, match, page), count in sorted(per_page.items(), key=str):
        out.append(f"  {team:12}  {match!s:>5}  {page:16}  {count:>6}")
    out += ["", "Changes made, whole period:"]
    per_edit = {}
    for (day, team, role, kind), count in edits.items():
        key = (team or "-", role, kind)
        per_edit[key] = per_edit.get(key, 0) + count
    out.append(f"  {'team':12}  {'who':7}  {'change':12}  {'times':>5}")
    for (team, role, kind), count in sorted(per_edit.items(), key=str):
        out.append(f"  {team:12}  {role:7}  {kind:12}  {count:>5}")
    return "\n".join(out) + "\n"
