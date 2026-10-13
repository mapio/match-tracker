from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from match_tracker.rules import (
    Counts,
    Draft,
    Event,
    RuleError,
    Window,
    check,
    delete,
    derive,
    reopen,
    set_player,
    set_timeout_team,
)
from match_tracker.teams import Rules

ROME = ZoneInfo("Europe/Rome")
RULES = Rules(best_of=5, points=25, last_set_points=15, win_by=2, timeouts_per_set=2)
PLAYERS = {"anna", "bea"}
# 15:00 in Rome on 10 October is 13:00 UTC
WINDOW = Window.for_match(datetime(2026, 10, 10, 15, 0), ROME)
NOW = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)


def apply(events, draft, now=NOW, rules=RULES):
    """The log after a request, as the server would append it."""
    for added in check(rules, WINDOW, PLAYERS, events, draft, now):
        events = (
            *events,
            Event(
                len(events) + 1, now, added.set, added.type, added.team, added.player
            ),
        )
    return events


def play(*drafts, events=(), rules=RULES):
    for draft in drafts:
        events = apply(events, draft, rules=rules)
    return events


def play_set(events, n, us, them, rules=RULES):
    """Start set n and play it to us-them, alternating, then the leader's rest."""
    drafts = [Draft(n, "set_start")]
    for _ in range(min(us, them)):
        drafts += [Draft(n, "point", "us"), Draft(n, "point", "them")]
    leader = "us" if us > them else "them"
    drafts += [Draft(n, "point", leader)] * abs(us - them)
    return play(*drafts, events=events, rules=rules)


def code(function, *args):
    with pytest.raises(RuleError) as error:
        function(*args)
    return error.value.code


def state_of(events, now=NOW, rules=RULES):
    return derive(rules, WINDOW, events, now)


def test_window_is_an_hour_before_to_a_day_after_in_the_team_zone():
    assert WINDOW.opens_at == datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert WINDOW.locks_at == datetime(2026, 10, 11, 13, 0, tzinfo=UTC)


def test_window_spans_a_clock_change_in_real_time():
    # Rome goes back one hour on 25 October at 03:00: 24 real hours after 01:30
    window = Window.for_match(datetime(2026, 10, 25, 1, 30), ROME)
    assert window.locks_at - window.opens_at == timedelta(hours=25)
    assert window.locks_at == datetime(2026, 10, 25, 23, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "offset, expected",
    [
        (timedelta(minutes=-61), "not_open_yet"),
        (timedelta(minutes=-59), None),
        (timedelta(0), None),
        (timedelta(hours=23, minutes=59), None),
        (timedelta(hours=24), "locked"),
        (timedelta(hours=24, minutes=1), "locked"),
    ],
)
def test_a_write_outside_the_window_is_refused(offset, expected):
    now = NOW + offset
    if expected is None:
        assert check(RULES, WINDOW, PLAYERS, (), Draft(1, "set_start"), now)
    else:
        assert (
            code(check, RULES, WINDOW, PLAYERS, (), Draft(1, "set_start"), now)
            == expected
        )


def test_a_fresh_match_is_upcoming_and_editable_in_the_window():
    state = state_of(())
    assert (state.status, state.current, state.sets) == ("upcoming", 1, ())
    assert state.editable
    assert not state_of((), NOW - timedelta(hours=3)).editable


def test_scores_timeouts_and_current_set_come_from_the_log():
    events = play(
        Draft(1, "set_start"),
        Draft(1, "point", "us", "anna"),
        Draft(1, "point", "them"),
        Draft(1, "point", "us"),
        Draft(1, "timeout", "them"),
    )
    state = state_of(events)
    first = state.sets[0]
    assert (first.score, first.timeouts) == (Counts(2, 1), Counts(0, 1))
    assert (first.closed, first.winner, state.status, state.current) == (
        False,
        None,
        "live",
        1,
    )


def test_a_winning_point_closes_the_set_and_the_next_one_waits():
    events = play_set((), 1, 25, 23)
    state = state_of(events)
    assert events[-1].type == "set_end" and events[-1].t == events[-2].t
    assert (state.sets[0].closed, state.sets[0].winner) == (True, "us")
    assert (state.sets_won, state.current, state.status) == (Counts(1, 0), 2, "live")


@pytest.mark.parametrize(
    "us, them, closes",
    [(25, 24, False), (26, 24, True), (25, 23, True), (24, 25, False), (30, 28, True)],
)
def test_a_set_needs_the_points_and_the_margin(us, them, closes):
    events = play_set((), 1, us, them)
    assert state_of(events).sets[0].closed is closes


def test_the_deciding_set_is_played_to_fifteen():
    events = ()
    for n, (us, them) in enumerate([(25, 20), (20, 25), (25, 20), (20, 25)], start=1):
        events = play_set(events, n, us, them)
    state = state_of(events)
    assert state.sets_won == Counts(2, 2) and state.status == "live"
    events = play_set(events, 5, 15, 13)
    state = state_of(events)
    assert (state.sets[4].closed, state.sets_won, state.status) == (
        True,
        Counts(3, 2),
        "over",
    )


def test_three_sets_end_a_best_of_five_and_a_best_of_three_ends_at_two():
    events = ()
    for n in (1, 2, 3):
        events = play_set(events, n, 25, 10)
    assert state_of(events).status == "over"
    short = Rules(3, 25, 15, 2, 2)
    events = play_set(play_set((), 1, 25, 10, short), 2, 25, 10, short)
    assert state_of(events, rules=short).status == "over"


def test_a_closed_set_stays_closed_when_a_fix_makes_its_score_non_winning():
    events = play_set((), 1, 25, 23)
    point = next(e for e in events if e.type == "point" and e.team == "us")
    fixed = delete(WINDOW, events, point.id, NOW)
    state = state_of(fixed)
    assert state.sets[0].closed and state.sets[0].score == Counts(24, 23)


def test_a_set_still_open_at_the_lock_is_derived_closed_and_the_match_over():
    events = play(Draft(1, "set_start"), Draft(1, "point", "us"))
    state = state_of(events, WINDOW.locks_at)
    assert state.sets[0].closed and state.sets[0].ended_at == WINDOW.locks_at
    assert (state.status, state.editable, state.sets_won) == (
        "over",
        False,
        Counts(1, 0),
    )
    assert events == play(Draft(1, "set_start"), Draft(1, "point", "us"))  # untouched
    assert state_of((), WINDOW.locks_at).status == "over"


def test_a_changed_date_moves_the_window_and_unlocks_the_match():
    events = play(Draft(1, "set_start"))
    later = Window.for_match(datetime(2026, 10, 12, 15, 0), ROME)
    assert derive(RULES, later, events, WINDOW.locks_at).status == "live"


@pytest.mark.parametrize(
    "draft, expected",
    [
        (Draft(2, "set_start"), "previous_set_open"),
        (Draft(1, "set_start"), "set_already_started"),
        (Draft(6, "set_start"), "invalid_event"),
        (Draft(0, "point", "us"), "invalid_event"),
        (Draft(1, "flip", "us"), "invalid_event"),
        (Draft(1, "point"), "invalid_event"),
        (Draft(1, "point", "them", "anna"), "invalid_event"),
        (Draft(1, "point", "us", "zoe"), "unknown_player"),
        (Draft(1, "set_end", "us"), "invalid_event"),
        (Draft(3, "point", "us"), "set_not_started"),
    ],
)
def test_requests_that_break_a_rule(draft, expected):
    events = play(Draft(1, "set_start"))
    assert code(check, RULES, WINDOW, PLAYERS, events, draft, NOW) == expected


def test_nothing_before_the_set_starts_and_nothing_in_a_closed_set():
    assert code(check, RULES, WINDOW, PLAYERS, (), Draft(1, "point", "us"), NOW) == (
        "set_not_started"
    )
    events = play(Draft(1, "set_start"), Draft(1, "set_end"))
    assert code(
        check, RULES, WINDOW, PLAYERS, events, Draft(1, "point", "us"), NOW
    ) == ("set_closed")
    assert code(
        check, RULES, WINDOW, PLAYERS, events, Draft(1, "timeout", "us"), NOW
    ) == ("set_closed")


def test_the_timeouts_of_a_team_are_limited_per_set():
    events = play(
        Draft(1, "set_start"), Draft(1, "timeout", "us"), Draft(1, "timeout", "us")
    )
    assert code(
        check, RULES, WINDOW, PLAYERS, events, Draft(1, "timeout", "us"), NOW
    ) == ("timeout_limit")
    assert check(RULES, WINDOW, PLAYERS, events, Draft(1, "timeout", "them"), NOW)
    next_set = apply(apply(events, Draft(1, "set_end")), Draft(2, "set_start"))
    assert check(RULES, WINDOW, PLAYERS, next_set, Draft(2, "timeout", "us"), NOW)


def test_a_set_starts_only_after_the_previous_one_is_closed():
    events = play(Draft(1, "set_start"), Draft(1, "set_end"))
    assert apply(events, Draft(2, "set_start"))[-1].set == 2


def test_nothing_is_accepted_once_the_match_is_over():
    events = ()
    for n in (1, 2, 3):
        events = play_set(events, n, 25, 10)
    assert code(check, RULES, WINDOW, PLAYERS, events, Draft(4, "set_start"), NOW) == (
        "match_over"
    )


def test_match_end_closes_an_open_set_and_ends_the_match():
    events = play(Draft(1, "set_start"), Draft(1, "point", "us"), Draft(1, "match_end"))
    assert [e.type for e in events[-2:]] == ["set_end", "match_end"]
    state = state_of(events)
    assert (state.status, state.current) == ("over", 1)
    assert code(
        check, RULES, WINDOW, PLAYERS, events, Draft(1, "point", "us"), NOW
    ) == ("match_over")
    assert code(check, RULES, WINDOW, PLAYERS, (), Draft(1, "match_end"), NOW) == (
        "set_not_started"
    )


def test_match_end_after_a_closed_set_adds_only_itself():
    events = play(Draft(1, "set_start"), Draft(1, "set_end"), Draft(1, "match_end"))
    assert [e.type for e in events] == ["set_start", "set_end", "match_end"]


def test_deleting_a_point_or_a_timeout_but_never_a_start_or_a_stop():
    events = play(
        Draft(1, "set_start"), Draft(1, "point", "us"), Draft(1, "timeout", "them")
    )
    assert [e.id for e in delete(WINDOW, events, 2, NOW)] == [1, 3]
    assert [e.id for e in delete(WINDOW, events, 3, NOW)] == [1, 2]
    assert code(delete, WINDOW, events, 1, NOW) == "protected_event"
    closed = apply(events, Draft(1, "set_end"))
    assert code(delete, WINDOW, closed, 4, NOW) == "protected_event"
    assert code(delete, WINDOW, events, 99, NOW) == "invalid_event"


def test_fixes_work_on_a_closed_set():
    events = play_set((), 1, 25, 23)
    anna = set_player(WINDOW, PLAYERS, events, 2, "anna", NOW)
    assert anna[1].player == "anna"
    assert set_player(WINDOW, PLAYERS, anna, 2, None, NOW)[1].player is None


def test_attributing_a_point():
    events = play(
        Draft(1, "set_start"), Draft(1, "point", "us"), Draft(1, "point", "them")
    )
    assert set_player(WINDOW, PLAYERS, events, 2, "bea", NOW)[1].player == "bea"
    assert code(set_player, WINDOW, PLAYERS, events, 2, "zoe", NOW) == "unknown_player"
    assert code(set_player, WINDOW, PLAYERS, events, 3, "bea", NOW) == "invalid_event"
    assert code(set_player, WINDOW, PLAYERS, events, 1, "bea", NOW) == "invalid_event"


def test_giving_a_timeout_to_the_other_team_unless_it_has_none_left():
    events = play(
        Draft(1, "set_start"),
        Draft(1, "timeout", "us"),
        Draft(1, "timeout", "them"),
        Draft(1, "timeout", "them"),
    )
    assert (
        code(set_timeout_team, RULES, WINDOW, events, 2, "them", NOW) == "timeout_limit"
    )
    assert set_timeout_team(RULES, WINDOW, events, 3, "us", NOW)[2].team == "us"
    assert set_timeout_team(RULES, WINDOW, events, 3, "them", NOW)[2].team == "them"
    assert (
        code(set_timeout_team, RULES, WINDOW, events, 3, "both", NOW) == "invalid_event"
    )
    assert (
        code(set_timeout_team, RULES, WINDOW, events, 1, "us", NOW) == "invalid_event"
    )


def test_reopening_a_set_removes_its_stop_and_a_match_end_after_it():
    events = play(
        Draft(1, "set_start"),
        Draft(1, "point", "us"),
        Draft(1, "set_end"),
        Draft(1, "match_end"),
    )
    reopened = reopen(WINDOW, events, 1, NOW)
    assert [e.type for e in reopened] == ["set_start", "point"]
    state = state_of(reopened)
    assert (state.sets[0].closed, state.status, state.current) == (False, "live", 1)
    assert code(reopen, WINDOW, reopened, 1, NOW) == "invalid_event"
    assert code(reopen, WINDOW, events, 2, NOW) == "invalid_event"


def test_reopening_an_earlier_set_makes_it_the_current_one():
    events = play_set(play_set((), 1, 25, 10), 2, 10, 25)
    events = apply(events, Draft(3, "set_start"))
    assert state_of(events).current == 3
    assert state_of(reopen(WINDOW, events, 1, NOW)).current == 1


def test_every_fix_is_refused_outside_the_window():
    events = play(Draft(1, "set_start"), Draft(1, "point", "us"), Draft(1, "set_end"))
    for now, expected in [
        (NOW - timedelta(hours=2), "not_open_yet"),
        (WINDOW.locks_at, "locked"),
    ]:
        assert code(delete, WINDOW, events, 2, now) == expected
        assert code(set_player, WINDOW, PLAYERS, events, 2, "anna", now) == expected
        assert code(set_timeout_team, RULES, WINDOW, events, 2, "us", now) == expected
        assert code(reopen, WINDOW, events, 1, now) == expected
