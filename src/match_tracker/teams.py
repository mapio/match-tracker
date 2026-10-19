"""Team files: validation, access tokens and reloading (see docs/contract.md).

A team file is parsed once into frozen objects, so the rest of the server never
touches raw JSON; anything that breaks the contract is a ConfigError naming the
file and the path inside it.
"""

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import journal

log = logging.getLogger("match-tracker")

TOKEN_ALPHABET = "acdefhmnprtu"
TOKEN_LENGTH = 6
COACH_TOKEN_LENGTH = 10
ADMIN_TOKEN_LENGTH = 14
ROLES = ("family", "staff")
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
RULE_KEYS = ("best_of", "points", "last_set_points", "win_by", "timeouts_per_set")


class ConfigError(Exception):
    """A team file breaks the contract; the message says where."""


def _join(path, key):
    return f"{path}.{key}" if path else key


def _object(value, path, required, optional=()):
    where = path or "top level"
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected an object")
    for key in required:
        if key not in value:
            raise ConfigError(f"{where}: missing '{key}'")
    for key in value:
        if key not in (*required, *optional):
            raise ConfigError(f"{_join(path, key)}: unknown key")
    return value


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: expected a non-empty string")
    return value


def _integer(value, path, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{path}: expected an integer >= {minimum}")
    return value


def _language(value, languages):
    if languages is not None and value not in languages:
        raise ConfigError(f"lang: no strings for '{value}' in i18n.json")
    return _text(value, "lang")


def _contact(value):
    parts = urlsplit(value) if isinstance(value, str) else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError("contact: expected an http or https URL")
    return value


def _demo(value):
    if not isinstance(value, bool):
        raise ConfigError("demo: expected true or false")
    return value


def _identifier(value, path):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ConfigError(f"{path}: use lowercase letters, digits, '-' and '_'")
    return value


@dataclass(frozen=True)
class Rules:
    best_of: int
    points: int
    last_set_points: int
    win_by: int
    timeouts_per_set: int

    @classmethod
    def parse(cls, raw, path, base=None):
        """The rules in `raw`; with a `base`, a partial override of it."""
        keys = _object(raw, path, () if base else RULE_KEYS, RULE_KEYS if base else ())
        values = {
            key: _integer(
                keys[key], _join(path, key), 0 if key == "timeouts_per_set" else 1
            )
            for key in keys
        }
        rules = cls(**({**asdict(base), **values} if base else values))
        if rules.best_of % 2 == 0:
            raise ConfigError(f"{_join(path, 'best_of')}: must be odd")
        return rules


@dataclass(frozen=True)
class Colors:
    us: str
    them: str


@dataclass(frozen=True)
class Location:
    name: str
    address: str | None


@dataclass(frozen=True)
class Match:
    n: int
    date: datetime  # local wall time of the team's timezone, no zone attached
    opponent: str
    location: Location
    players: tuple[str, ...]
    rules: Rules


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    lang: str
    timezone: ZoneInfo
    colors: Colors
    show_scorers: bool
    rules: Rules
    roster: dict[str, str]
    matches: tuple[Match, ...]
    contact: str | None = None  # where a visitor with a wrong link can ask
    demo: bool = False  # staff may wipe a match: for showing the app, not for use

    @classmethod
    def parse(cls, team_id, raw, languages=None):
        _identifier(team_id, "team id")
        top = _object(
            raw,
            "",
            (
                "name",
                "lang",
                "timezone",
                "colors",
                "show_scorers",
                "rules",
                "roster",
                "matches",
            ),
            ("contact", "demo"),
        )
        try:
            timezone = ZoneInfo(_text(top["timezone"], "timezone"))
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"timezone: unknown zone '{top['timezone']}'") from None
        colors = _object(top["colors"], "colors", ("us", "them"))
        if not isinstance(top["show_scorers"], bool):
            raise ConfigError("show_scorers: expected true or false")
        rules = Rules.parse(top["rules"], "rules")
        roster = top["roster"]
        if not isinstance(roster, dict) or not roster:
            raise ConfigError("roster: no players")
        for player_id, name in roster.items():
            _identifier(player_id, f"roster.{player_id}")
            _text(name, f"roster.{player_id}")
        if not isinstance(top["matches"], list):
            raise ConfigError("matches: expected a list")
        matches = tuple(
            _parse_match(item, f"matches[{i}]", roster, rules)
            for i, item in enumerate(top["matches"])
        )
        numbers = [m.n for m in matches]
        for n in set(numbers):
            if numbers.count(n) > 1:
                raise ConfigError(f"matches: number {n} is used twice")
        return cls(
            id=team_id,
            name=_text(top["name"], "name"),
            lang=_language(top["lang"], languages),
            timezone=timezone,
            colors=Colors(
                _text(colors["us"], "colors.us"), _text(colors["them"], "colors.them")
            ),
            show_scorers=top["show_scorers"],
            rules=rules,
            roster=dict(roster),
            matches=matches,
            contact=_contact(top["contact"]) if "contact" in top else None,
            demo=_demo(top.get("demo", False)),
        )

    def match(self, n):
        return next((m for m in self.matches if m.n == n), None)


def _parse_match(raw, path, roster, team_rules):
    obj = _object(
        raw,
        path,
        ("n", "date", "opponent", "location", "players"),
        ("rules",),
    )
    try:
        date = datetime.strptime(str(obj["date"]), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ConfigError(
            f"{path}.date: expected YYYY-MM-DDTHH:MM, got {obj['date']!r}"
        ) from None
    place = _object(obj["location"], f"{path}.location", ("name",), ("address",))
    if not isinstance(obj["players"], list):
        raise ConfigError(f"{path}.players: expected a list")
    players = tuple(obj["players"])
    for i, player in enumerate(players):
        if player not in roster:
            raise ConfigError(f"{path}.players[{i}]: '{player}' is not in the roster")
        if player in players[:i]:
            raise ConfigError(f"{path}.players[{i}]: '{player}' is listed twice")
    return Match(
        n=_integer(obj["n"], f"{path}.n"),
        date=date,
        opponent=_text(obj["opponent"], f"{path}.opponent"),
        location=Location(
            _text(place["name"], f"{path}.location.name"),
            _text(place["address"], f"{path}.location.address")
            if "address" in place
            else None,
        ),
        players=players,
        rules=Rules.parse(obj["rules"], f"{path}.rules", team_rules)
        if "rules" in obj
        else team_rules,
    )


def _slug(text, length=TOKEN_LENGTH):
    number = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
    digits = []
    while number:
        number, digit = divmod(number, len(TOKEN_ALPHABET))
        digits.append(TOKEN_ALPHABET[digit])
    return "".join(reversed(digits))[:length]


def coach_token(team_id, salt):
    """The longer token of a team's coach or DA, who sets up the team; none without
    a salt."""
    return _slug(f"{salt}:{team_id}:coach", COACH_TOKEN_LENGTH) if salt else None


def admin_token(salt):
    """The token of the one admin, for the whole server; none without a salt."""
    return _slug(f"{salt}:admin", ADMIN_TOKEN_LENGTH) if salt else None


def _unused_slug(seed, taken):
    slug, attempt = _slug(seed), 0
    while slug in taken:
        attempt += 1
        slug = _slug(f"{seed}:{attempt}")
    return slug


def derive_tokens(team_ids, salt=""):
    """{team id: {role: slug}}, the same for the same ids and salt.

    A secret salt keeps someone who knows a team id from computing its tokens."""
    taken = set()
    tokens = {}
    for team_id in sorted(team_ids):
        for role in ROLES:
            seed = f"{salt}:{team_id}:{role}" if salt else f"{team_id}:{role}"
            slug = _unused_slug(seed, taken)
            taken.add(slug)
            tokens.setdefault(team_id, {})[role] = slug
    return tokens


def read_salt(teams_dir):
    """The secret in the file `salt` beside the team files, or "" if there is none.

    Keep that file: the tokens, so the links families hold, depend on it."""
    path = Path(teams_dir) / "salt"
    if not path.exists():
        return ""
    salt = path.read_text(encoding="utf-8").strip()
    if not salt:
        raise ConfigError(f"{path}: empty")
    return salt


def _read(path, languages=None):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"{path}: {error}") from None
    try:
        return Team.parse(path.stem, raw, languages)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from None


def _check_against_logs(team, data_dir):
    """A team file may not drop a match that has a log, or a player in one."""
    for log_path in sorted((data_dir / team.id).glob("*.json")):
        try:
            events = json.loads(log_path.read_text(encoding="utf-8"))["events"]
            n = int(log_path.stem)
        except (OSError, ValueError, KeyError, TypeError):
            raise ConfigError(f"{log_path}: not a readable event log") from None
        if team.match(n) is None:
            raise ConfigError(f"match {n} has a log and cannot be dropped")
        for event in events:
            player = event.get("player")
            if player is not None and player not in team.roster:
                raise ConfigError(f"player '{player}' is in the log of match {n}")


TOP_ORDER = (
    "name",
    "lang",
    "timezone",
    "colors",
    "contact",
    "demo",
    "show_scorers",
    "rules",
    "roster",
    "matches",
)
MATCH_ORDER = ("n", "date", "opponent", "location", "players", "rules")


def _in_order(data, order):
    """`data` with the keys of `order` first, in that order: a file is laid out the
    same whoever sent it, so a hand edit afterwards diffs cleanly."""
    known = {key: data[key] for key in order if key in data}
    return {**known, **{k: v for k, v in data.items() if k not in known}}


def _write(path, raw, keep):
    """Write a team file in the formatter's layout, atomically, after copying the
    current one to `<file>.bak` when `keep`."""
    data = _in_order(raw, TOP_ORDER)
    if isinstance(data.get("matches"), list):
        data["matches"] = [
            _in_order(m, MATCH_ORDER) if isinstance(m, dict) else m
            for m in data["matches"]
        ]
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if keep and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    scratch = path.with_name(path.name + ".tmp")
    with scratch.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    scratch.replace(path)


class _Entry:
    def __init__(self, path, team, mtime_ns):
        self.path = path
        self.team = team
        self.mtime_ns = mtime_ns
        self.refused_mtime_ns = None


class Teams:
    """The team files of a directory, reloaded when one changes."""

    def __init__(
        self,
        teams_dir,
        data_dir,
        languages=None,
        salt="",
        now=lambda: datetime.now(UTC),
    ):
        self.on_reload = None  # called with the team id after an accepted reload
        self._now = now
        self._languages = languages  # the keys of i18n.json; None skips the check
        self._teams_dir = Path(teams_dir)
        self._data_dir = Path(data_dir)
        self._salt = salt
        self._entries = {}
        for path in sorted(Path(teams_dir).glob("*.json")):
            team = _read(path, languages)
            try:
                _check_against_logs(team, self._data_dir)
            except ConfigError as error:
                raise ConfigError(f"{path}: {error}") from None
            self._entries[team.id] = _Entry(path, team, path.stat().st_mtime_ns)
        if not self._entries:
            raise ConfigError(f"{teams_dir}: no team files")
        self._set_tokens()

    def _set_tokens(self):
        self.tokens = derive_tokens(self._entries, self._salt)
        self._by_token = {
            slug: (team_id, role)
            for team_id, roles in self.tokens.items()
            for role, slug in roles.items()
        }

    def ids(self):
        return list(self._entries)

    def raw(self, team_id):
        """The team file as it is on disk, as JSON data, for a page to edit."""
        return json.loads(self._entries[team_id].path.read_text(encoding="utf-8"))

    def _journal(self, team_id, who, op, before):
        record = journal.team_record(self._now(), who, op, before)
        journal.append(journal.path_of(self._data_dir, team_id, journal.TEAM), record)

    def save(self, team_id, raw, *, who="", op="save"):
        """Replace a team's file by `raw`, once it passes every check a start-up
        would apply; a ConfigError names what is wrong and nothing is written. The
        file as it was goes to the team's journal first."""
        entry = self._entries[team_id]
        team = self._validated(team_id, raw)
        self._journal(team_id, who, op, self.raw(team_id))
        _write(entry.path, raw, keep=True)
        entry.team, entry.mtime_ns = team, entry.path.stat().st_mtime_ns
        entry.refused_mtime_ns = None
        log.info("%s saved", entry.path.name)
        if self.on_reload is not None:
            self.on_reload(team_id)
        return team

    def create(self, team_id, raw, *, who=""):
        """Add a team. Its tokens must not move those of the teams already here."""
        if team_id in self._entries:
            raise ConfigError(f"team id '{team_id}': already exists")
        team = self._validated(team_id, raw)
        after = derive_tokens([*self._entries, team_id], self._salt)
        if any(after[known] != roles for known, roles in self.tokens.items()):
            raise ConfigError(
                f"team id '{team_id}': its links would clash; pick another"
            )
        path = self._teams_dir / f"{team_id}.json"
        self._journal(team_id, who, "create", None)
        _write(path, raw, keep=False)
        self._entries[team_id] = _Entry(path, team, path.stat().st_mtime_ns)
        self._set_tokens()
        log.info("%s created", path.name)
        return team

    def _validated(self, team_id, raw):
        team = Team.parse(team_id, raw, self._languages)
        _check_against_logs(team, self._data_dir)
        return team

    def team(self, team_id):
        entry = self._entries[team_id]
        self._refresh(entry)
        return entry.team

    def find(self, token):
        """(team, role) for a token, case-insensitively, or None."""
        found = self._by_token.get(token.lower())
        return None if found is None else (self.team(found[0]), found[1])

    def coach_token(self, team_id):
        return coach_token(team_id, self._salt)

    def find_coach(self, token):
        """The team whose coach this token belongs to, or None."""
        for team_id in self._entries:
            if self._salt and token.lower() == coach_token(team_id, self._salt):
                return self.team(team_id)
        return None

    def is_admin(self, token):
        return bool(self._salt) and token.lower() == admin_token(self._salt)

    @property
    def admin_token(self):
        return admin_token(self._salt)

    def markdown(self, base):
        """The links to hand out, as a Markdown page with clickable links."""
        base = base.rstrip("/")

        def link(path):
            return f"[{path}]({base}{path})"

        columns = ["Family", "Staff", *(["Coach"] if self._salt else [])]
        rows = []
        for team_id, roles in self.tokens.items():
            paths = [f"/{roles[r]}" for r in ROLES]
            if self._salt:
                paths.append(f"/{self.coach_token(team_id)}")
            rows.append(
                f"| {self.team(team_id).name} (`{team_id}`) | "
                + " | ".join(link(p) for p in paths)
                + " |"
            )
        return "\n".join(
            [
                "# Links",
                "",
                f"Landing page: <{base}/>",
                *(["", f"Admin: {link('/' + self.admin_token)}"] if self._salt else []),
                "",
                "| Team | " + " | ".join(columns) + " |",
                "| --- |" + " --- |" * len(columns),
                *rows,
                "",
            ]
        )

    def _refresh(self, entry):
        try:
            mtime_ns = entry.path.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if mtime_ns in (entry.mtime_ns, entry.refused_mtime_ns):
            return
        try:
            team = _read(entry.path, self._languages)
            _check_against_logs(team, self._data_dir)
        except ConfigError as error:
            entry.refused_mtime_ns = mtime_ns
            log.warning("%s; the previous version stays in force", error)
            return
        entry.team, entry.mtime_ns = team, mtime_ns
        log.info("%s reloaded", entry.path.name)
        if self.on_reload is not None:
            self.on_reload(team.id)
