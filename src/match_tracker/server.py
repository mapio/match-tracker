"""The server: the pages, the API and the live stream of docs/contract.md."""

import argparse
import asyncio
import json
import logging
import sys
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import journal, oplog, rules, views
from .limiter import Limiter
from .rules import Draft, Event, RuleError
from .store import Log, Store
from .teams import ConfigError, Teams, read_salt

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("match-tracker")
UNPROCESSABLE = ("invalid_event",)  # the other rule codes are 409
UNKNOWN_TOKEN_LIMIT = 10  # requests with an unknown token, per address, per window
UNKNOWN_TOKEN_WINDOW = 60  # seconds
# keep a copy, but ask the server before using it: the pages and scripts change
# with the code, and a browser left to guess would run a stale script for hours
REVALIDATE = {"Cache-Control": "no-cache"}
# the home-screen app of each role: its name, its short name (what fits under the
# icon) and the suffix of its icons' files, so that the apps are told apart on a
# home screen; the family's is the plain one
APPS = {
    "family": ("MatchTracker", "MatchTracker", ""),
    "staff": ("MatchTrackerStaff", "MTStaff", "-staff"),
    "coach": ("MatchTrackerCoach", "MTCoach", "-coach"),
    "admin": ("MatchTrackerAdmin", "MTAdmin", "-admin"),
}
# the icons of those apps: an operator's own, beside the team files, win
ICONS = tuple(
    f"icon-{size}{suffix}.png"
    for _, _, suffix in APPS.values()
    for size in ("192", "512", "maskable-512")
)
PNG = {"type": "image/png"}
THEME_COLOR = BACKGROUND_COLOR = "#ffffff"  # the pages are Pico's white


class RevalidatedFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = REVALIDATE["Cache-Control"]
        return response


class NewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set: StrictInt
    type: StrictStr
    team: StrictStr | None = None


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    address: StrictStr | None = None


class SeasonMatch(BaseModel):
    """A match as the season page sends it; `n` is absent for a new one."""

    model_config = ConfigDict(extra="forbid")
    n: StrictInt | None = None
    date: StrictStr
    opponent: StrictStr
    location: Place


class ColorsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    us: StrictStr
    them: StrictStr


class RulesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    best_of: StrictInt
    points: StrictInt
    last_set_points: StrictInt
    win_by: StrictInt
    timeouts_per_set: StrictInt


class TeamInfo(BaseModel):
    """What the team form edits; the matches and `demo` are left as they are."""

    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    lang: StrictStr
    timezone: StrictStr
    colors: ColorsIn
    contact: StrictStr | None = None
    rules: RulesIn


class NewTeam(TeamInfo):
    id: StrictStr


class Season(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matches: list[SeasonMatch]


class Fix(BaseModel):
    """The team a timeout is given to, or that serves first; that it is sent is checked later."""

    model_config = ConfigDict(extra="forbid")
    team: StrictStr | None = None


class ApiError(Exception):
    """An answer with the contract's error body."""

    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def create_app(
    teams_dir,
    data_dir,
    now=lambda: datetime.now(UTC),
    keepalive=20,
    salt="",
):
    """The app; a test can fix `now` and shorten the stream's `keepalive` seconds."""
    languages = set(json.loads((STATIC / "i18n.json").read_text(encoding="utf-8")))
    teams = Teams(teams_dir, data_dir, languages, salt, now)
    store = Store(data_dir, now)
    # the address of the request in hand: only the limiter sees it, and keeps it
    # in memory
    caller = ContextVar("caller", default="?")

    async def remember_caller(request: Request):
        caller.set(request.client.host if request.client else "?")

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(remember_caller)],
    )
    app.state.teams = teams
    limiter = Limiter(
        UNKNOWN_TOKEN_LIMIT, UNKNOWN_TOKEN_WINDOW, lambda: now().timestamp()
    )
    watchers = {}  # (team id, match number) -> the queues of its open streams

    def notify(team_id, n=None):
        """Wake the streams of a match, or of all the matches of a team."""
        for (watched_team, watched_n), queues in watchers.items():
            if watched_team == team_id and n in (None, watched_n):
                for queue in queues:
                    with suppress(asyncio.QueueFull):  # one pending wake-up is enough
                        queue.put_nowait(None)

    teams.on_reload = notify

    def error_page(status):
        return FileResponse(
            STATIC / "error.html", status_code=status, headers=REVALIDATE
        )

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        if not request.url.path.startswith("/api/"):  # a browser, not a script
            return error_page(error.status)
        body = {"error": error.code, "detail": error.detail}
        return JSONResponse(body, status_code=error.status)

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request, error):
        if error.status_code == 404 and not request.url.path.startswith("/api/"):
            return error_page(404)
        return await http_exception_handler(request, error)

    @app.exception_handler(Exception)
    async def unexpected(request, error):
        # the route's template names the page, never the path as typed (a token)
        where = getattr(request.scope.get("route"), "path", "?")
        log.error("unhandled error in %s", where, exc_info=error)
        failure = ApiError(500, "server_error", "something went wrong")
        if request.url.path.startswith("/api/"):
            body = {"error": failure.code, "detail": failure.detail}
            return JSONResponse(body, status_code=failure.status)
        page = "<!doctype html><html lang=it><meta charset=utf-8><title>Errore</title>"
        page += "<p>Si è verificato un errore. Riprova tra poco.</p></html>"
        return HTMLResponse(page, status_code=failure.status)

    @app.exception_handler(RuleError)
    async def rule_error(request, error):
        status = 422 if error.code in UNPROCESSABLE else 409
        body = {"error": error.code, "detail": error.detail}
        return JSONResponse(body, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def malformed(request, error):
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"][1:]) or "body"
        body = {"error": "invalid_event", "detail": f"{where}: {first['msg']}"}
        return JSONResponse(body, status_code=422)

    refused = {"count": 0, "noted": None}  # the limit's refusals, noted once a minute

    def note_refusal():
        """Say in the log that the limit refused requests, without saying whose."""
        refused["count"] += 1
        at = now().timestamp()
        if refused["noted"] is None or at - refused["noted"] >= 60:
            log.warning(
                "unknown-token limit: %d request(s) refused since the last note",
                refused["count"],
            )
            refused["count"], refused["noted"] = 0, at

    def unknown_token():
        """A token that opens nothing counts against the limit like any other."""
        if not limiter.allow(caller.get()):
            note_refusal()
            raise ApiError(429, "rate_limited", "too many unknown tokens")
        raise ApiError(404, "not_found", "not found")

    def team_of(token):
        found = teams.find(token)
        if found is None:
            unknown_token()
        return found

    def match_of(token, n):
        team, role = team_of(token)
        match = team.match(int(n)) if n.isascii() and n.isdigit() else None
        if match is None:
            raise ApiError(404, "not_found", "not found")
        return team, role, match

    app.mount("/static", RevalidatedFiles(directory=STATIC), name="static")

    @app.get("/favicon.ico")
    async def favicon():
        # browsers ask for it on every page; it names no token, so it must not count
        return Response(status_code=204)

    def page(name):
        return FileResponse(STATIC / name, headers=REVALIDATE)

    def page_beside_teams(name, fallback=None):
        """A file the operator keeps beside the team files, else `fallback` (a
        path) if given; no token, so it is not counted against the limit either."""
        page = Path(teams_dir) / name
        if not page.is_file():
            page = fallback
        if page is None:
            raise StarletteHTTPException(404)
        return FileResponse(page, headers=REVALIDATE)

    @app.get("/privacy")
    async def privacy():
        return page_beside_teams("privacy.html")

    @app.get("/install")
    async def install():
        return page("install.html")

    @app.get("/icons/{name}")
    async def icons(name: str):
        if name not in ICONS:
            raise StarletteHTTPException(404)
        return page_beside_teams(name, STATIC / "icons" / name)

    @app.get("/demo")
    async def demo():
        return page_beside_teams("demo.html")

    @app.get("/chat/{team_id}")
    async def chat(team_id: str):
        # the contact's address stays here, out of every page and answer
        found = team_id in teams.ids() and teams.team(team_id).contact
        if not found:
            raise ApiError(404, "not_found", "not found")
        return RedirectResponse(
            found, status_code=302, headers={"Cache-Control": "no-store"}
        )

    def coach_of(token):
        team = teams.find_coach(token)
        if team is None:
            unknown_token()
        return team

    def admin_of(token):
        if not teams.is_admin(token):
            unknown_token()

    def with_info(raw, info):
        """`raw` with what the team form edits replaced by `info`; the matches are
        kept."""
        data = info.model_dump(exclude={"id"})
        raw = {**raw, **{k: data[k] for k in ("name", "lang", "timezone", "colors")}}
        raw["rules"] = data["rules"]
        if data["contact"]:
            raw["contact"] = data["contact"]
        else:
            raw.pop("contact", None)
        raw.setdefault("matches", [])
        return raw

    # The token alone says what it is for: six letters read a team (family) or score
    # it (staff), ten are a coach's, fourteen the admin's. So a URL carries no word
    # for the role, and the same path means what its token allows.

    @app.post("/api/{token}", status_code=201)
    async def api_admin_create(token: str, body: NewTeam):
        admin_of(token)
        try:
            team = teams.create(body.id, with_info({}, body), who="admin")
        except ConfigError as error:
            raise ApiError(422, "invalid_event", str(error)) from None
        log.info("%s created by the admin", team.id)
        return views.team_info(team, teams.raw(team.id), languages)

    @app.get("/api/{token}/team")
    async def api_coach_team(token: str):
        team = coach_of(token)
        return views.team_info(team, teams.raw(team.id), languages)

    @app.put("/api/{token}/team")
    async def api_coach_team_change(token: str, body: TeamInfo):
        team = coach_of(token)
        try:
            team = teams.save(
                team.id,
                with_info(teams.raw(team.id), body),
                who="coach",
                op="team",
            )
        except ConfigError as error:
            raise ApiError(422, "invalid_event", str(error)) from None
        log.info("%s team form saved by the coach", team.id)
        return views.team_info(team, teams.raw(team.id), languages)

    @app.get("/api/{token}/season")
    async def api_coach_season(token: str):
        return views.season(coach_of(token), store, now())

    @app.put("/api/{token}/season")
    async def api_coach_season_change(token: str, body: Season):
        team = coach_of(token)
        raw = teams.raw(team.id)
        old = {m["n"]: m for m in raw["matches"]}
        unknown = [m.n for m in body.matches if m.n is not None and m.n not in old]
        if unknown:
            raise ApiError(422, "invalid_event", f"no match {unknown[0]} to change")
        free = max([*old, *(m.n for m in body.matches if m.n is not None)], default=0)
        matches = []
        for item in body.matches:
            place = {"name": item.location.name}
            if item.location.address:
                place["address"] = item.location.address
            fields = {"date": item.date, "opponent": item.opponent, "location": place}
            if item.n is None:
                free += 1
                entry = {"n": free}
            else:
                entry = dict(old[item.n])
                if all(entry.get(k) == v for k, v in fields.items()):
                    matches.append(entry)  # untouched, whatever its state
                    continue
                if views.match_closed(team, team.match(item.n), now()):
                    raise ApiError(409, "match_closed", f"match {item.n} is closed")
            matches.append({**entry, **fields})
            try:  # a match is never moved to a time that is already open for scoring
                start = datetime.strptime(item.date, "%Y-%m-%dT%H:%M")
            except ValueError:
                continue  # the validator says what is wrong with the date
            if rules.Window.for_match(start, team.timezone).opens_at <= now():
                raise ApiError(409, "match_closed", "the date is already past")
        raw["matches"] = matches
        try:
            team = teams.save(team.id, raw, who="coach", op="season")
        except ConfigError as error:
            raise ApiError(422, "invalid_event", str(error)) from None
        log.info("%s season saved by the coach", team.id)
        return views.season(team, store, now())

    @app.get("/api/contacts")
    async def api_contacts():
        return views.contacts([teams.team(i) for i in teams.ids()])

    @app.get("/api/{token}")
    async def api_list(token: str):
        if teams.is_admin(token):
            return {"teams": views.admin_teams(teams), "languages": sorted(languages)}
        coach = teams.find_coach(token)
        if coach is not None:
            return views.coach_dashboard(coach)
        team, role = team_of(token)
        return views.match_list(team, role, store, now())

    @app.get("/api/{token}/{n}")
    async def api_match(token: str, n: str):
        team, role, match = match_of(token, n)
        record = store.get(team.id, match.n)
        return views.match_state(team, match, role, record, now())

    @app.get("/api/{token}/{n}/stream")
    async def api_stream(token: str, n: str):
        team, role, match = match_of(token, n)
        key = (team.id, match.n)

        def current():
            team_now = teams.team(team.id)
            match_now = team_now.match(match.n)
            if match_now is None:
                return None
            record = store.get(*key)
            return views.match_state(team_now, match_now, role, record, now())

        async def events():
            # A state goes out whenever it differs from the last one sent, whether
            # a write woke us or time did (the window, the lock); else a comment.
            sent = None
            queue = asyncio.Queue(maxsize=1)
            watchers.setdefault(key, set()).add(queue)
            log.info("%s/%d watcher joined (%d)", *key, len(watchers[key]))
            try:
                while (state := current()) is not None:
                    if state != sent:
                        sent = state
                        yield f"event: state\ndata: {json.dumps(state)}\n\n"
                    else:
                        yield ": keepalive\n\n"
                    with suppress(TimeoutError):
                        await asyncio.wait_for(queue.get(), keepalive)
            finally:
                watchers[key].discard(queue)
                log.info("%s/%d watcher left (%d)", *key, len(watchers[key]))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def staff_match(token, n):
        team, role, match = match_of(token, n)
        if role != "staff":
            raise ApiError(404, "not_found", "not found")
        return team, match

    def number(text, what):
        if not (text.isascii() and text.isdigit()):
            raise RuleError("invalid_event", f"{what} must be a number")
        return int(text)

    def change(team, match, kind, what, build):
        """Apply `build(log, window, at) -> Log` to a match's log and save it."""
        record = store.get(team.id, match.n)
        at = now()
        try:
            new = build(record, views.window_of(team, match), at)
        except RuleError as error:
            log.info("%s/%d refused %s", team.id, match.n, error.code)
            raise
        store.put(team.id, match.n, new, who="staff", op=kind)
        log.info("%s/%d %s", team.id, match.n, what)  # only staff can score
        notify(team.id, match.n)
        return views.match_state(team, match, "staff", new, at)

    @app.post("/api/{token}/{n}/events", status_code=201)
    async def api_add(token: str, n: str, body: NewEvent):
        team, match = staff_match(token, n)
        draft = Draft(body.set, body.type, body.team)

        def build(record, window, at):
            added = rules.check(match.rules, window, record.events, draft, at)
            events = tuple(
                Event(record.next_id + i, at, d.set, d.type, d.team)
                for i, d in enumerate(added)
            )
            return Log(record.next_id + len(added), (*record.events, *events))

        what = " ".join(p for p in (body.type, body.team) if p)
        return change(team, match, body.type, f"{what} set {body.set}", build)

    @app.patch("/api/{token}/{n}/events/{event_id}")
    async def api_fix(token: str, n: str, event_id: str, body: Fix):
        team, match = staff_match(token, n)
        number(event_id, "event id")
        if body.model_fields_set != {"team"}:
            raise RuleError("invalid_event", "send the 'team' to change")

        def build(record, window, at):
            events = rules.set_team(
                match.rules, window, record.events, int(event_id), body.team, at
            )
            return Log(record.next_id, events)

        return change(team, match, "fix", f"fix event {event_id}", build)

    @app.delete("/api/{token}/{n}/events/{event_id}")
    async def api_delete(token: str, n: str, event_id: str):
        team, match = staff_match(token, n)
        number(event_id, "event id")

        def build(record, window, at):
            events = rules.delete(window, record.events, int(event_id), at)
            return Log(record.next_id, events)

        return change(team, match, "delete", f"delete event {event_id}", build)

    @app.post("/api/{token}/{n}/sets/{set_number}/reopen")
    async def api_reopen(token: str, n: str, set_number: str):
        team, match = staff_match(token, n)
        number(set_number, "set")

        def build(record, window, at):
            events = rules.reopen(window, record.events, int(set_number), at)
            return Log(record.next_id, events)

        return change(team, match, "reopen", f"reopen set {set_number}", build)

    @app.post("/api/{token}/{n}/reset")
    async def api_reset(token: str, n: str):
        team, match = staff_match(token, n)
        if not team.demo:
            raise ApiError(404, "not_found", "not found")
        store.reset(team.id, match.n, who="staff")
        log.info("%s/%d reset", team.id, match.n)
        notify(team.id, match.n)
        return views.match_state(
            team, match, "staff", store.get(team.id, match.n), now()
        )

    @app.get("/")
    async def page_landing():
        return FileResponse(STATIC / "error.html", headers=REVALIDATE)

    @app.get("/{token}")
    async def page_list(token: str):
        if teams.is_admin(token):
            return page("admin.html")
        if teams.find_coach(token) is not None:
            return page("coach.html")
        team_of(token)  # a token of no team is refused here
        return page("list.html")

    # before `/{token}/{n}`, which would take the name for a match number
    @app.get("/{token}/manifest.webmanifest")
    async def manifest(token: str):
        if teams.is_admin(token):
            role = "admin"
        elif teams.find_coach(token) is not None:
            role = "coach"
        else:
            _, role = team_of(token)  # a token of no team is refused here
        name, short_name, suffix = APPS[role]
        start = f"/{token}"
        body = {
            "id": start,
            "name": name,
            "short_name": short_name,
            "display": "standalone",
            "start_url": start,
            "scope": start,
            "theme_color": THEME_COLOR,
            "background_color": BACKGROUND_COLOR,
            "icons": [
                {"src": f"/icons/icon-192{suffix}.png", "sizes": "192x192", **PNG},
                {"src": f"/icons/icon-512{suffix}.png", "sizes": "512x512", **PNG},
                {
                    "src": f"/icons/icon-maskable-512{suffix}.png",
                    "sizes": "512x512",
                    "purpose": "maskable",
                    **PNG,
                },
            ],
        }
        return JSONResponse(
            body, media_type="application/manifest+json", headers=REVALIDATE
        )

    @app.get("/{token}/{n}/chart")
    async def page_chart(token: str, n: str):
        match_of(token, n)
        return page("chart.html")

    @app.get("/{token}/{n}")
    async def page_match(token: str, n: str):
        if teams.is_admin(token) and n == "new":
            return page("admin.html")
        if teams.find_coach(token) is not None and n in ("season", "team"):
            return page("season.html" if n == "season" else "coach.html")
        match_of(token, n)
        return page("match.html")

    return app


def links(argv):
    """`match-tracker urls`: print the links of every team as a Markdown page."""
    parser = argparse.ArgumentParser(
        prog="match-tracker urls", description=links.__doc__
    )
    parser.add_argument("--teams", type=Path, default=Path("teams"))
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:8000",
        help="the address the links start with",
    )
    args = parser.parse_args(argv)
    try:
        teams = Teams(args.teams, args.data, salt=read_salt(args.teams))
    except (OSError, ConfigError) as error:
        sys.exit(f"error: {error}")
    print(teams.markdown(args.base), end="")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["urls"]:  # a subcommand; without one the server starts
        return links(argv[1:])
    if argv[:1] == ["replay"]:
        return journal.show(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teams", type=Path, default=Path("teams"))
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument(
        "--logs", type=Path, default=Path("logs"), help="where the operator log goes"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        oplog.setup(args.logs)  # from here nothing is written to the console
        app = create_app(args.teams, args.data, salt=read_salt(args.teams))
    except (OSError, ConfigError) as error:
        sys.exit(f"error: {error}")  # the one thing said on the console
    teams = app.state.teams
    played = len(list(args.data.glob("*/*.json")))
    log.info(
        "started: %d team(s) (%s), %d match(es) with a log",
        len(teams.ids()),
        ", ".join(teams.ids()),
        played,
    )
    # an open stream is a response that never completes: without a limit uvicorn
    # waits for it for ever when asked to stop
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        # its own logging setup would add a console handler, so the journal:
        # oplog.setup has already sent every record to logs/app.log
        log_config=None,
        log_level="info",
        # its access log writes each visitor's address and the whole URL, which
        # carries the secret links: the privacy notice says neither is written
        access_log=False,
        timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
