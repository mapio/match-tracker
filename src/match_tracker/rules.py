"""The rules of a match as pure functions over its event log (docs/contract.md).

Nothing here reads the clock or a file: the caller passes the time and the log,
so every rule is tested with plain values. Each public function either returns
the result or raises RuleError with a code from the contract.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

OPENS_BEFORE = timedelta(minutes=60)
LOCKS_AFTER = timedelta(hours=24)
TEAMS = ("us", "them")
TYPES = ("set_start", "point", "timeout", "set_end", "match_end")


class RuleError(Exception):
    """A request breaks a rule; `code` is one of the contract's error codes."""

    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Draft:
    """An event as requested: the server adds its id and time when appending."""

    set: int
    type: str
    team: str | None = None


@dataclass(frozen=True)
class Event:
    id: int
    t: datetime
    set: int
    type: str
    team: str | None = None


@dataclass(frozen=True)
class Counts:
    us: int = 0
    them: int = 0

    def of(self, team):
        return self.us if team == "us" else self.them

    def plus(self, team):
        return replace(self, **{team: self.of(team) + 1})


@dataclass(frozen=True)
class Window:
    """When writes are accepted: from `opens_at` up to, not including, `locks_at`."""

    opens_at: datetime
    locks_at: datetime
    starts_at: datetime

    @classmethod
    def for_match(cls, date, zone):
        # through UTC, so that a clock change in between does not shift the span
        start = date.replace(tzinfo=zone).astimezone(UTC)
        return cls(start - OPENS_BEFORE, start + LOCKS_AFTER, start)

    def is_open(self, now):
        return self.opens_at <= now < self.locks_at

    def check(self, now):
        if now < self.opens_at:
            raise RuleError("not_open_yet", f"opens at {self.opens_at.isoformat()}")
        if now >= self.locks_at:
            raise RuleError("locked", f"locked since {self.locks_at.isoformat()}")


@dataclass(frozen=True)
class SetState:
    set: int
    started_at: datetime
    ended_at: datetime | None
    closed: bool
    score: Counts
    timeouts: Counts
    serving: str | None
    winner: str | None


@dataclass(frozen=True)
class MatchState:
    sets: tuple[SetState, ...]
    sets_won: Counts
    current: int
    status: str  # upcoming, live or over
    editable: bool


def _winner(score):
    if score.us == score.them:
        return None
    return "us" if score.us > score.them else "them"


def derive(rules, window, events, now):
    """Everything the screens show, from the log; a set open at the lock is closed."""
    locked = now >= window.locks_at
    started, ended = {}, {}
    points, timeouts = {}, {}
    serving = {}  # the first server, then whoever scored last
    match_ended = False
    for event in events:
        if event.type == "set_start":
            if event.set not in started:
                serving[event.set] = event.team  # None in a log from before the team
            started.setdefault(event.set, event.t)
        elif event.type == "set_end":
            ended.setdefault(event.set, event.t)
        elif event.type == "point":
            points[event.set] = points.get(event.set, Counts()).plus(event.team)
            if serving.get(event.set) is not None:  # not in an old log
                serving[event.set] = event.team
        elif event.type == "timeout":
            timeouts[event.set] = timeouts.get(event.set, Counts()).plus(event.team)
        else:
            match_ended = True
    sets = []
    for n in sorted(started):
        ended_at = ended.get(n, window.locks_at if locked else None)
        score = points.get(n, Counts())
        sets.append(
            SetState(
                set=n,
                started_at=started[n],
                ended_at=ended_at,
                closed=ended_at is not None,
                score=score,
                timeouts=timeouts.get(n, Counts()),
                serving=None if ended_at is not None else serving[n],
                winner=_winner(score) if ended_at is not None else None,
            )
        )
    won = Counts(
        sum(s.winner == "us" for s in sets), sum(s.winner == "them" for s in sets)
    )
    over = locked or match_ended or max(won.us, won.them) > rules.best_of // 2
    current = 1
    while any(s.set == current and s.closed for s in sets):
        current += 1
    if over and sets:
        current = min(current, sets[-1].set)
    return MatchState(
        sets=tuple(sets),
        sets_won=won,
        current=current,
        status="over" if over else ("live" if events else "upcoming"),
        editable=window.is_open(now),
    )


def _wins(rules, set_number, score):
    target = rules.last_set_points if set_number == rules.best_of else rules.points
    high, low = max(score.us, score.them), min(score.us, score.them)
    return high >= target and high - low >= rules.win_by


def _invalid(detail):
    return RuleError("invalid_event", detail)


def check(rules, window, events, draft, now):
    """The events to append for a requested one: it, plus an automatic set_end."""
    window.check(now)
    if draft.type not in TYPES:
        raise _invalid(f"unknown event type '{draft.type}'")
    if not 1 <= draft.set <= rules.best_of:
        raise _invalid(f"set must be between 1 and {rules.best_of}")
    has_team = draft.type in ("set_start", "point", "timeout")
    if has_team != (draft.team is not None) or (has_team and draft.team not in TEAMS):
        raise _invalid(f"'{draft.type}' {'needs' if has_team else 'takes no'} a team")
    state = derive(rules, window, events, now)
    if state.status == "over":
        raise RuleError("match_over", "the match is over")
    sets = {s.set: s for s in state.sets}
    this = sets.get(draft.set)
    if draft.type == "set_start":
        if this is not None:
            raise RuleError("set_already_started", f"set {draft.set} has started")
        previous = sets.get(draft.set - 1)
        if draft.set > 1 and (previous is None or not previous.closed):
            raise RuleError("previous_set_open", f"set {draft.set - 1} is not closed")
        return [draft]
    if this is None:
        raise RuleError("set_not_started", f"set {draft.set} has not started")
    if draft.type == "match_end":
        closing = [] if this.closed else [Draft(draft.set, "set_end")]
        return [*closing, draft]
    if this.closed:
        raise RuleError("set_closed", f"set {draft.set} is closed")
    if (
        draft.type == "timeout"
        and this.timeouts.of(draft.team) >= rules.timeouts_per_set
    ):
        raise RuleError("timeout_limit", f"no timeouts left for '{draft.team}'")
    if draft.type == "point" and _wins(rules, draft.set, this.score.plus(draft.team)):
        return [draft, Draft(draft.set, "set_end")]
    return [draft]


def _find(events, event_id):
    for index, event in enumerate(events):
        if event.id == event_id:
            return index, event
    raise _invalid(f"no event {event_id}")


def delete(window, events, event_id, now):
    """The log without a point or a timeout; starts and stops are protected."""
    window.check(now)
    _, event = _find(events, event_id)
    if event.type not in ("point", "timeout"):
        raise RuleError("protected_event", f"a '{event.type}' cannot be deleted")
    return tuple(e for e in events if e.id != event_id)


def set_team(rules, window, events, event_id, team, now):
    """The log with a timeout given to the other team, if that has one left, or
    with another team serving first."""
    window.check(now)
    index, event = _find(events, event_id)
    if event.type not in ("timeout", "set_start"):
        raise _invalid("only a timeout or a set start has a team to change")
    if team not in TEAMS:
        raise _invalid(f"unknown team '{team}'")
    if event.type == "set_start":
        return (*events[:index], replace(event, team=team), *events[index + 1 :])
    used = sum(
        e.type == "timeout" and e.set == event.set and e.team == team for e in events
    )
    if team != event.team and used >= rules.timeouts_per_set:
        raise RuleError("timeout_limit", f"no timeouts left for '{team}'")
    return (*events[:index], replace(event, team=team), *events[index + 1 :])


def reopen(window, events, set_number, now):
    """The log without the set_end of a set and without a match_end after it."""
    window.check(now)
    ends = [
        i for i, e in enumerate(events) if e.type == "set_end" and e.set == set_number
    ]
    if not ends:
        raise _invalid(f"set {set_number} is not closed")
    return tuple(
        e
        for i, e in enumerate(events)
        if i != ends[0] and not (i > ends[0] and e.type == "match_end")
    )
