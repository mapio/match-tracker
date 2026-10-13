import json
from datetime import UTC, datetime

from match_tracker.rules import Event
from match_tracker.store import Log, Store, event_to_json, format_time

T = datetime(2026, 10, 10, 13, 0, 5, 123000, tzinfo=UTC)
EVENTS = (
    Event(1, T, 1, "set_start"),
    Event(2, T, 1, "point", "us", "anna"),
    Event(3, T, 1, "point", "them"),
)


def test_times_are_utc_with_milliseconds_and_z():
    assert format_time(T) == "2026-10-10T13:00:05.123Z"


def test_absent_fields_are_left_out_of_an_event():
    assert event_to_json(EVENTS[0]) == {
        "id": 1,
        "t": "2026-10-10T13:00:05.123Z",
        "set": 1,
        "type": "set_start",
    }
    assert event_to_json(EVENTS[1])["player"] == "anna"


def test_a_match_without_a_file_has_an_empty_log(tmp_path):
    assert Store(tmp_path).get("rho", 1) == Log(1, ())


def test_a_log_survives_a_restart(tmp_path):
    Store(tmp_path).put("rho", 3, Log(4, EVENTS))
    again = Store(tmp_path).get("rho", 3)
    assert again == Log(4, EVENTS)


def test_the_file_holds_next_id_and_the_events(tmp_path):
    Store(tmp_path).put("rho", 3, Log(4, EVENTS))
    raw = json.loads((tmp_path / "rho" / "3.json").read_text())
    assert raw["next_id"] == 4 and len(raw["events"]) == 3
    assert [p.name for p in (tmp_path / "rho").iterdir()] == ["3.json"]


def test_put_replaces_the_log_in_memory_and_on_disk(tmp_path):
    store = Store(tmp_path)
    store.put("rho", 1, Log(2, EVENTS[:1]))
    store.put("rho", 1, Log(4, EVENTS))
    assert store.get("rho", 1).events == EVENTS
    assert Store(tmp_path).get("rho", 1).next_id == 4
