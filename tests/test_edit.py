"""Saving and creating team files through `Teams`, the write path the editing
pages share."""

import copy
import json
import shutil
from pathlib import Path

import pytest
from test_teams import VALID

from match_tracker import teams as teams_module
from match_tracker.teams import ConfigError, Teams

EXAMPLE = Path(__file__).parent.parent / "teams" / "rho.json"


def make(tmp_path, **changes):
    folder = tmp_path / "teams"
    folder.mkdir(exist_ok=True)
    (folder / "rho.json").write_text(json.dumps({**copy.deepcopy(VALID), **changes}))
    return Teams(folder, tmp_path / "data"), folder / "rho.json"


def test_a_saved_file_has_the_layout_of_the_formatter(tmp_path):
    folder = tmp_path / "teams"
    folder.mkdir()
    shutil.copy(EXAMPLE, folder / "rho.json")
    teams = Teams(folder, tmp_path / "data")
    original = (folder / "rho.json").read_text(encoding="utf-8")
    # keys sent in another order, and a non-ASCII name, come out as the file has them
    raw = teams.raw("rho")
    shuffled = {key: raw[key] for key in reversed(list(raw))}
    teams.save("rho", shuffled)
    assert (folder / "rho.json").read_text(encoding="utf-8") == original


def test_a_refused_save_writes_nothing(tmp_path):
    teams, path = make(tmp_path)
    before = path.read_text()
    raw = teams.raw("rho")
    raw["matches"][0]["date"] = "tomorrow"
    with pytest.raises(ConfigError, match=r"matches\[0\]\.date"):
        teams.save("rho", raw)
    assert path.read_text() == before
    assert not path.with_name("rho.json.bak").exists()
    assert teams.team("rho").matches[0].date.year == 2026


def test_a_save_keeps_the_previous_version_once_and_takes_effect(tmp_path):
    teams, path = make(tmp_path)
    seen = []
    teams.on_reload = seen.append
    first = path.read_text()
    raw = teams.raw("rho")
    raw["name"] = "Second"
    teams.save("rho", raw)
    assert (
        json.loads(path.with_name("rho.json.bak").read_text())["name"] == "Volley Test"
    )
    assert path.with_name("rho.json.bak").read_text() == first
    raw["name"] = "Third"
    teams.save("rho", raw)
    assert json.loads(path.with_name("rho.json.bak").read_text())["name"] == "Second"
    assert teams.team("rho").name == "Third" and seen == ["rho", "rho"]


def test_a_save_may_not_drop_a_match_that_has_a_log(tmp_path):
    teams, _ = make(tmp_path)
    log_dir = tmp_path / "data" / "rho"
    log_dir.mkdir(parents=True)
    (log_dir / "1.json").write_text(json.dumps({"next_id": 1, "events": []}))
    raw = teams.raw("rho")
    raw["matches"] = [m for m in raw["matches"] if m["n"] != 1]
    with pytest.raises(ConfigError, match="match 1 has a log"):
        teams.save("rho", raw)
    assert any(m.n == 1 for m in teams.team("rho").matches)


def test_the_backup_and_the_scratch_file_are_not_team_files(tmp_path):
    teams, path = make(tmp_path)
    raw = teams.raw("rho")
    teams.save("rho", raw)
    assert sorted(p.name for p in path.parent.iterdir()) == ["rho.json", "rho.json.bak"]
    assert Teams(path.parent, tmp_path / "data").ids() == ["rho"]


def test_a_new_team_gets_its_file_and_its_links_and_the_others_keep_theirs(tmp_path):
    teams, path = make(tmp_path)
    before = copy.deepcopy(teams.tokens)
    raw = copy.deepcopy(VALID)
    raw["name"] = "New team"
    teams.create("tau", raw)
    assert (path.parent / "tau.json").exists()
    assert teams.find(teams.tokens["tau"]["staff"])[0].name == "New team"
    assert {k: v for k, v in teams.tokens.items() if k != "tau"} == before
    # and it survives a restart with the same links
    again = Teams(path.parent, tmp_path / "data")
    assert again.tokens == teams.tokens


def test_a_team_id_in_use_or_invalid_or_clashing_is_refused(tmp_path, monkeypatch):
    teams, path = make(tmp_path)
    with pytest.raises(ConfigError, match="already exists"):
        teams.create("rho", copy.deepcopy(VALID))
    with pytest.raises(ConfigError, match="team id"):
        teams.create("Bad Id", copy.deepcopy(VALID))
    assert not (path.parent / "Bad Id.json").exists()
    real = teams_module._slug
    monkeypatch.setattr(
        teams_module,
        "_slug",
        # only the first attempt of aaa's family slug collides: later ones differ
        lambda text: real("rho:family") if text == "aaa:family" else real(text),
    )
    with pytest.raises(ConfigError, match="would clash"):
        teams.create("aaa", copy.deepcopy(VALID))
    assert not (path.parent / "aaa.json").exists()
    assert teams.ids() == ["rho"]
