import copy
import json
import logging
import os

import pytest

from match_tracker.teams import (
    TOKEN_ALPHABET,
    TOKEN_LENGTH,
    ConfigError,
    Team,
    Teams,
    derive_tokens,
    read_salt,
)

VALID = {
    "name": "Volley Test",
    "lang": "it",
    "timezone": "Europe/Rome",
    "colors": {"us": "#111111", "them": "#eeeeee"},
    "show_scorers": True,
    "rules": {
        "best_of": 5,
        "points": 25,
        "last_set_points": 15,
        "win_by": 2,
        "timeouts_per_set": 2,
    },
    "roster": {"anna": "Anna", "bea": "Bea", "chiara": "Chiara"},
    "matches": [
        {
            "n": 1,
            "date": "2026-10-10T15:00",
            "opponent": "Opponent",
            "location": {"name": "Gym", "address": "Via Roma 1, Milano"},
            "players": ["anna", "bea"],
        },
        {
            "n": 2,
            "date": "2026-10-17T15:00",
            "opponent": "Other",
            "location": {"name": "Hall"},
            "players": ["bea", "chiara"],
            "rules": {"best_of": 3},
        },
    ],
}


def changed(**top):
    raw = copy.deepcopy(VALID)
    raw.update(top)
    return raw


def write(path, raw):
    path.write_text(json.dumps(raw), encoding="utf-8")


def bump_mtime(path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_valid_file_parses_into_objects():
    team = Team.parse("test", VALID)
    assert team.name == "Volley Test"
    assert str(team.timezone) == "Europe/Rome"
    assert team.match(1).players == ("anna", "bea")
    assert team.match(1).rules.best_of == 5
    assert team.match(2).rules.best_of == 3  # override merged into the team rules
    assert team.match(2).rules.points == 25
    assert team.match(2).location.address is None
    assert team.match(3) is None


@pytest.mark.parametrize(
    "raw, message",
    [
        (changed(extra=1), "extra: unknown key"),
        ({k: v for k, v in VALID.items() if k != "timezone"}, "missing 'timezone'"),
        (changed(timezone="Mars/Olympus"), "timezone: unknown zone"),
        (changed(show_scorers="yes"), "show_scorers: expected true or false"),
        (changed(colors={"us": "red"}), "colors: missing 'them'"),
        (changed(roster={}), "roster: no players"),
        (changed(roster={"Anna": "Anna"}), "roster.Anna: use lowercase"),
        (
            changed(rules={**VALID["rules"], "best_of": 4}),
            "rules.best_of: must be odd",
        ),
        (
            changed(rules={**VALID["rules"], "points": 0}),
            "rules.points: expected an integer >= 1",
        ),
        (
            changed(rules={k: v for k, v in VALID["rules"].items() if k != "win_by"}),
            "rules: missing 'win_by'",
        ),
        (changed(matches="none"), "matches: expected a list"),
    ],
)
def test_team_errors_name_the_path(raw, message):
    with pytest.raises(ConfigError, match=message):
        Team.parse("test", raw)


def match_with(**fields):
    raw = copy.deepcopy(VALID)
    raw["matches"][0].update(fields)
    return raw


@pytest.mark.parametrize(
    "raw, message",
    [
        (match_with(date="10/10/2026"), r"matches\[0\].date: expected"),
        (match_with(date="2026-13-10T15:00"), r"matches\[0\].date: expected"),
        (match_with(players=["anna", "zoe"]), r"matches\[0\].players\[1\]: 'zoe'"),
        (match_with(players=["anna", "anna"]), r"matches\[0\].players\[1\]: 'anna' is"),
        (match_with(players="anna"), r"matches\[0\].players: expected a list"),
        (match_with(n=0), r"matches\[0\].n: expected an integer >= 1"),
        (match_with(n=True), r"matches\[0\].n: expected an integer"),
        (match_with(n=2), "number 2 is used twice"),
        (match_with(location={"address": "x"}), r"matches\[0\].location: missing"),
        (match_with(rules={"best_of": 2}), r"matches\[0\].rules.best_of: must be odd"),
        (match_with(rules={"bonus": 1}), r"matches\[0\].rules.bonus: unknown key"),
        (match_with(opponent=""), r"matches\[0\].opponent: expected"),
    ],
)
def test_match_errors_name_the_path(raw, message):
    with pytest.raises(ConfigError, match=message):
        Team.parse("test", raw)


def test_a_language_without_strings_is_refused_when_the_languages_are_known():
    assert Team.parse("test", VALID, {"it", "en"}).lang == "it"
    assert Team.parse("test", VALID).lang == "it"  # not checked without the keys
    with pytest.raises(ConfigError, match="lang: no strings for 'it'"):
        Team.parse("test", VALID, {"en"})


@pytest.mark.parametrize("team_id", ["Rho", "rho team", "-rho", ""])
def test_bad_team_id(team_id):
    with pytest.raises(ConfigError, match="team id"):
        Team.parse(team_id, VALID)


def test_tokens_are_stable_and_in_the_alphabet():
    tokens = derive_tokens(["tau", "rho"])
    assert tokens == derive_tokens(["rho", "tau"])
    for roles in tokens.values():
        for slug in roles.values():
            assert len(slug) == TOKEN_LENGTH
            assert set(slug) <= set(TOKEN_ALPHABET)


def test_tokens_do_not_change_between_versions():
    # links already handed out depend on these values
    assert derive_tokens(["rho"]) == {"rho": {"family": "cnfrda", "staff": "cmfnph"}}


def test_a_salt_changes_the_tokens_and_keeps_them_stable():
    plain = derive_tokens(["rho"])
    salted = derive_tokens(["rho"], "s3cret")
    assert salted == derive_tokens(["rho"], "s3cret")  # the same at every start
    assert salted != plain and salted != derive_tokens(["rho"], "other")
    assert derive_tokens(["rho"], "") == plain  # no salt: the unsalted tokens


def test_the_salt_is_read_from_a_file_beside_the_team_files(tmp_path):
    assert read_salt(tmp_path) == ""
    (tmp_path / "salt").write_text("s3cret\n")
    assert read_salt(tmp_path) == "s3cret"  # the newline is not part of it
    (tmp_path / "salt").write_text(" \n")
    with pytest.raises(ConfigError, match="salt: empty"):
        read_salt(tmp_path)


def test_teams_with_a_salt_answer_to_the_salted_tokens_only(tmp_path):
    teams_dir, data_dir = tmp_path / "teams", tmp_path / "data"
    teams_dir.mkdir()
    (teams_dir / "rho.json").write_text(json.dumps(VALID))
    teams = Teams(teams_dir, data_dir, salt="s3cret")
    assert teams.tokens == derive_tokens(["rho"], "s3cret")
    assert teams.find(derive_tokens(["rho"])["rho"]["family"]) is None
    assert teams.find(teams.tokens["rho"]["staff"])[1] == "staff"


def test_tokens_never_repeat_across_roles_and_teams():
    ids = [f"team{i}" for i in range(300)]
    slugs = [s for roles in derive_tokens(ids).values() for s in roles.values()]
    assert len(slugs) == len(set(slugs)) == 600


def test_a_collision_takes_the_next_attempt(monkeypatch):
    from match_tracker import teams

    real = teams._slug
    monkeypatch.setattr(
        teams, "_slug", lambda text: "aaaaaa" if ":" not in text[-2:] else real(text)
    )
    # every first attempt collapses onto one slug; later attempts differ
    tokens = derive_tokens(["a", "b"])
    slugs = [s for roles in tokens.values() for s in roles.values()]
    assert len(set(slugs)) == 4 and slugs.count("aaaaaa") == 1


@pytest.fixture
def folders(tmp_path):
    (tmp_path / "teams").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path / "teams", tmp_path / "data"


def test_teams_load_find_and_print_urls(folders):
    teams_dir, data_dir = folders
    write(teams_dir / "rho.json", VALID)
    write(teams_dir / "alfa.json", changed(name="Alfa"))
    teams = Teams(teams_dir, data_dir)
    assert teams.ids() == ["alfa", "rho"]
    family = teams.tokens["rho"]["family"]
    team, role = teams.find(family.upper())
    assert (team.id, role) == ("rho", "family")
    assert teams.find("zzzzzz") is None
    assert not hasattr(teams, "urls")  # the links are printed by `match-tracker urls`


def test_a_bad_file_stops_the_start_naming_the_file(folders):
    teams_dir, data_dir = folders
    write(teams_dir / "rho.json", changed(timezone="Nowhere/Land"))
    with pytest.raises(ConfigError, match=r"rho\.json: timezone"):
        Teams(teams_dir, data_dir)


def test_no_team_files_stops_the_start(folders):
    with pytest.raises(ConfigError, match="no team files"):
        Teams(*folders)


def test_a_changed_call_up_is_seen_by_the_next_lookup(folders):
    teams_dir, data_dir = folders
    path = teams_dir / "rho.json"
    write(path, VALID)
    teams = Teams(teams_dir, data_dir)
    assert teams.team("rho").match(1).players == ("anna", "bea")
    raw = copy.deepcopy(VALID)
    raw["matches"][0]["players"] = ["anna", "bea", "chiara"]
    write(path, raw)
    bump_mtime(path)
    assert teams.team("rho").match(1).players == ("anna", "bea", "chiara")


def test_a_broken_file_keeps_the_old_one_and_logs_once(folders, caplog):
    teams_dir, data_dir = folders
    path = teams_dir / "rho.json"
    write(path, VALID)
    teams = Teams(teams_dir, data_dir)
    write(path, changed(timezone="Nowhere/Land"))
    bump_mtime(path)
    with caplog.at_level(logging.WARNING, logger="match-tracker"):
        assert teams.team("rho").name == "Volley Test"
        assert teams.team("rho").name == "Volley Test"
    assert len(caplog.records) == 1
    assert "previous version stays in force" in caplog.text
    write(path, changed(name="Fixed"))
    bump_mtime(path)
    bump_mtime(path)
    assert teams.team("rho").name == "Fixed"


def test_an_accepted_reload_calls_back_and_a_refused_one_does_not(folders):
    teams_dir, data_dir = folders
    path = teams_dir / "rho.json"
    write(path, VALID)
    teams = Teams(teams_dir, data_dir)
    seen = []
    teams.on_reload = seen.append
    write(path, changed(name="Second"))
    bump_mtime(path)
    teams.team("rho")
    assert seen == ["rho"]
    write(path, changed(timezone="Nowhere/Land"))
    bump_mtime(path)
    teams.team("rho")
    assert seen == ["rho"]


def write_log(data_dir, team_id, n, events):
    (data_dir / team_id).mkdir(exist_ok=True)
    write(data_dir / team_id / f"{n}.json", {"next_id": 9, "events": events})


def test_a_file_dropping_a_played_match_is_refused(folders):
    teams_dir, data_dir = folders
    path = teams_dir / "rho.json"
    write(path, VALID)
    write_log(data_dir, "rho", 1, [])
    teams = Teams(teams_dir, data_dir)
    raw = copy.deepcopy(VALID)
    del raw["matches"][0]
    write(path, raw)
    bump_mtime(path)
    assert teams.team("rho").match(1) is not None


def test_a_file_dropping_a_player_in_a_log_is_refused(folders):
    teams_dir, data_dir = folders
    path = teams_dir / "rho.json"
    write(path, VALID)
    write_log(data_dir, "rho", 1, [{"id": 1, "type": "point", "player": "bea"}])
    teams = Teams(teams_dir, data_dir)
    raw = copy.deepcopy(VALID)
    del raw["roster"]["bea"]
    raw["matches"] = [raw["matches"][0] | {"players": ["anna"]}]
    write(path, raw)
    bump_mtime(path)
    assert "bea" in teams.team("rho").roster


def test_a_start_with_a_log_the_file_contradicts_is_refused(folders):
    teams_dir, data_dir = folders
    write(teams_dir / "rho.json", VALID)
    write_log(data_dir, "rho", 7, [])
    with pytest.raises(ConfigError, match="match 7 has a log"):
        Teams(teams_dir, data_dir)


def test_the_real_team_files_are_valid():
    from pathlib import Path

    root = Path(__file__).parent.parent
    teams = Teams(root / "teams", root / "data-not-there")
    assert "rho" in teams.ids()


def test_the_links_page_lists_every_team_with_clickable_links(tmp_path):
    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()
    for team_id in ("rho", "tau"):
        (teams_dir / f"{team_id}.json").write_text(json.dumps(VALID))
    teams = Teams(teams_dir, tmp_path / "data")
    page = teams.markdown("https://example.org/")
    assert "Landing page: <https://example.org/>" in page
    for team_id, roles in teams.tokens.items():
        row = next(line for line in page.splitlines() if f"(`{team_id}`)" in line)
        assert row.startswith("| Volley Test (`")
        for role in ("family", "staff"):
            slug = roles[role]
            assert f"[/{slug}](https://example.org/{slug})" in row
    assert len([line for line in page.splitlines() if line.startswith("| Volley")]) == 2


def test_the_urls_subcommand_prints_the_page_with_the_salt_and_does_not_serve(
    tmp_path, capsys
):
    from match_tracker.server import main

    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()
    (teams_dir / "rho.json").write_text(json.dumps(VALID))
    main(["urls", "--teams", str(teams_dir), "--base", "https://example.org"])
    plain = capsys.readouterr().out
    (teams_dir / "salt").write_text("s3cret\n")
    main(["urls", "--teams", str(teams_dir), "--base", "https://example.org"])
    salted = capsys.readouterr().out
    slug = derive_tokens(["rho"])["rho"]["family"]
    assert f"https://example.org/{slug})" in plain
    assert (
        slug not in salted
        and derive_tokens(["rho"], "s3cret")["rho"]["staff"] in salted
    )
