# Contract

What the server, the staff page and the family pages agree on. A change here is
made first, then in the code that depends on it.

## Files

- `teams/<team>.json`: one configuration file per team; the team id is the file
  name without `.json`. Read at startup and again when it changes (see
  *Reloading*); written only by the editing pages (see *Editing*), which keep
  the previous version in `<team>.json.bak`.
- `data/<team>/<m>.json`, `<m>` the match number: the event log of one match,
  written by the server only.
- `data/<team>/<m>.journal.jsonl`: the journal of that match, one line per
  change of its log (the events added, removed and changed, whole, with the role
  that acted, what it did and the time), added and flushed to the disk before
  the log is replaced; `match-tracker replay <file>` prints the log it gives.
  Never read by the server.
- `data/<team>/team.journal.jsonl`: one line per save of the team file (team
  form, season, creation) with the content the file had before. Never read by
  the server.
- `i18n.json`: UI strings, `{lang: {key: text}}`, shared by all teams.

### Team file

- `name`: the team's name, shown on its button.
- `lang`: language code of the UI, a key of `i18n.json`.
- `timezone`: IANA name (`Europe/Rome`) in which the match `date`s are read.
- `colors`: `{us, them}`, CSS colors, used for both the team and its timeout
  button.
- `contact`: optional, an http or https URL (a chat link such as
  `https://wa.me/<number>`) where someone with a wrong link can ask. A team
  without it is not listed on the landing page. The address is never sent to a
  browser but by the redirect below, so a phone number in it is in no page or
  answer.
- `demo`: optional, `true` for a team used to show the app (temporary): its
  matches can be wiped by staff, and its `team` object carries `demo: true`.
- `rules`: `best_of`, `points`, `last_set_points`, `win_by`, `timeouts_per_set`.
  A match may override any of them with its own `rules`.
- `matches[]`: `n` (the match number, a positive integer, unique in the file and
  never changed once played; it is not the position in the list), `date` (local
  wall time, `YYYY-MM-DDTHH:MM`, no zone), `opponent` (name), `location`
  (`name`, optional `address`), optional `rules`.

The app holds no name of a person: a team file with a `roster`, `show_scorers`
or a match `players` is refused, naming the key.

The server validates the whole file at startup and names the offending path.

### Reloading

A team file can be edited while the server runs, for example to change the date
of a match. On each request the server compares the modification time of the
file of the team concerned with the one it loaded; when it differs, the file is
read again.

- A file that passes the validation replaces that team's configuration (name,
  colors, language, rules, matches) at once, and a `state` event goes to the
  streams of its matches. The derived state is recomputed from the logs with the
  new configuration; a closed set stays closed.
- A file that fails the validation, or that drops a match which has a log, is
  refused: the previous configuration stays in force and the server logs the
  reason once per change.
- A team file added or removed by hand while running is not noticed; that needs
  a restart, since the tokens are derived from the set of files. A team created
  on the admin page is added at once.

### Tokens

A team has two tokens, one for `staff` and one for `family`, never written in
the team file. Each is a slug of six lowercase letters from `acdefhmnprtu`:
common Italian letters that cannot be taken for a digit or for another letter. A
URL is matched case-insensitively. The server computes it: the SHA-256 of
`<team id>:<role>`, read as base-12 digits, first six. If a slug is already
taken (teams are processed in alphabetical order, roles `family` then `staff`)
it hashes `<team id>:<role>:<k>` with the next `k` from 1. So the slugs are the
same at every start, as long as the team file names do not change, and are
stored nowhere.

A file `salt` beside the team files, if there is one, holds a secret that
prefixes every hashed text (`<salt>:<team id>:<role>`), so that knowing a team
id does not give its tokens. Its content, minus surrounding whitespace, must not
be empty. It is not a team file, is never committed, and must be kept: the links
depend on it. Without it the tokens are the unsalted ones above. The server does
not log them; `match-tracker urls` prints the links of every team.

There are 12⁶ = 2,985,984 slugs. To make guessing impractical, requests with an
unknown token are limited per client address (as the server sees it, so behind a
proxy it runs with proxy headers on) to 10 per minute; the next ones get `429`.
Requests with a known token are not limited.

With a salt there are two more kinds of token, from the same alphabet and
matched the same way, for the pages that edit (*Editing*), each a higher level
than the one before:

- `family` reads; `staff` scores (anyone the bench trusts);
- the **coach** token of a team, 10 letters, for its coach or DA: the SHA-256 of
  `<salt>:<team id>:coach`, read as base-12 digits, first ten;
- the **admin** token, 14 letters, for the whole server: the same for
  `<salt>:admin`.

Without a salt neither exists. A URL has no word for the role: the token alone
decides what a path is for, by what it is (six letters, ten, fourteen), so
`/<tok>` is the match list, the coach's start page or the admin's list, and
every other path below is the same for the kind of token it names. The three
lengths cannot be mistaken for one another. A token that opens nothing, of any
length, counts against the same limit as an unknown one. A link cannot be
revoked alone: changing the salt changes every one.

## Event log

`data/<team>/<m>.json` is `{"next_id": k, "events": [...]}`. The server rewrites
it whole, atomically, after each change; `next_id` only grows, so an event id is
never reused.

An event is `{id, t, set, type, team?}`:

- `id`: integer from `next_id`.
- `t`: UTC time of the request, ISO 8601 with milliseconds and `Z`, set by the
  server.
- `set`: set number, from 1.
- `type`: one of
  - `set_start`: the set is opened;
  - `point`: needs `team` (`us` or `them`);
  - `timeout`: needs `team`;
  - `set_end`: the set is closed;
  - `match_end`: the match is over; `set` is the last set played, closed in the
    same request if it was open.
- Events are kept in order of creation.

Everything else is derived from the log.

### Rules enforced by the server

- A set can start only if it is set 1 or the previous set is closed, it is not
  started yet, and the match is not over.
- `point` and `timeout` need a started, open set.
- A team has at most `timeouts_per_set` timeouts in a set.
- A point that wins the set (`points`, or `last_set_points` in the deciding set,
  with a margin of `win_by`) appends a `set_end` in the same request, with the
  same `t`. The set stays closed even if a later fix makes the score no longer
  winning; only reopening opens it.
- A closed set is reopened only by the reopen action, which deletes its
  `set_end` and a `match_end` that follows it.
- `set_start`, `set_end` and `match_end` are never deleted or patched through
  the event endpoints.
- The match is over when a team has won `ceil(best_of / 2)` closed sets, a
  `match_end` exists, or the match is locked.

### Window

Writes are accepted only while the match is editable: from `opens_at`, 60
minutes before `date`, to `locks_at`, 24 hours after it, with `date` read in the
team's `timezone` and the time taken from the server's clock.

- Before `opens_at` every write is refused with `409 not_open_yet`; after
  `locks_at`, with `409 locked`. That includes the fix list and the reopen
  action. Reads are never refused.
- Nothing is written at the lock. A match that is locked counts as over, and a
  set still open at that moment is derived as closed, its `ended_at` being
  `locks_at`; its points count as they stand. The log is untouched, so the
  record holds only what the staffer did.
- Changing the `date` or the `timezone` in the team file moves the window; a
  match already locked is unlocked again if the new window says so.

## Derived state

What the server returns and streams. Staff and family differ only where noted.

- `role`: `staff` or `family`, from the token; the list carries it too.
- `team`: `{id, name, lang, colors}`.
- `match`:
  `{n, date, opponent, location, status, opens_at, locks_at, editable}`,
  `status` being `upcoming` (no event and not locked), `live` or `over`
  (including locked); `opens_at` and `locks_at` are UTC instants (ISO 8601,
  `Z`), `editable` whether now is within the window (a particular write may
  still break a rule).
- `rules`: the effective rules of the match.
- `sets`: one entry per set that has a `set_start`:
  `{set, started_at, ended_at|null, closed, score: {us, them}, timeouts: {us, them}, winner: "us"|"them"|null}`.
- `sets_won`: `{us, them}`.
- `current`: number of the set to show: the first not closed, 1 if none has
  started.
- Staff only: `events`, the whole log.
- Family only: `timeline`, the points and timeouts of every set in order,
  `{set, t, type, team}`; the chart takes the same from `events` for the staff.

## Endpoints

URLs are short because they are typed and sent around: a team is reached by its
token alone (`<tok>` below) and a match by its number (`<m>`), for example
`/hmdpet/3`. The token also decides the role: a staff token gives the staff
pages and may write; a family token gives the family pages and only reads. An
unknown token, an unknown match and a write with a family token give the same
`404`, so nothing can be probed.

### Pages

Static HTML, the same for every token; the page reads the token from its URL and
takes its role from the API's answer.

- `/`: the landing page: the teams that have a `contact`, each as a button to
  it, and no token or link to a team's pages.
- `/chat/<team id>`: `302` to the team's `contact`, `no-store`; `404` for an
  unknown team or one without a contact. Not counted by the limit.
- `/<tok>`: with a family or staff token, the match list; with a coach token,
  the coach's start page; with the admin token, the list of teams with their
  links.
- `/<tok>/<m>`: with a family or staff token, the family match view or the staff
  screen.
- `/<tok>/<m>/chart`: with a family or staff token, the chart of the points of
  the match, a line per team in each set; a read, so reachable also once the
  match is over. It is linked from the family match view and from the menu of
  the staff screen.
- With a coach token: `/<tok>/season` is the season and `/<tok>/team` the team's
  settings. These pages assume a tablet or a wider screen.
- With the admin token: `/<tok>/new` creates a team.
- `/privacy`: the privacy notice, the file `privacy.html` in the directory of
  the team files (`404` when it is not there); no token, and not counted by the
  limit on unknown tokens. The landing and the error pages link to it.
- `/demo`: likewise an optional page of the operator's, the file `demo.html`
  beside the team files (`404` when it is not there), for presenting the app.
- `/static/...`: scripts, style and `i18n.json`.
- `/favicon.ico`: `204`, no content; it names no token and is not counted by the
  limit on unknown tokens.
- `?lang=xx` overrides the team's language on any page.

A page request that ends in `404` or `429` (an unknown path, token or match, or
too many unknown tokens) is answered with the status and the HTML of the landing
page's script, which says the link does not work and whom to ask: the list of
contacts, or, for a known token with a wrong match number, that team's contact
and a link back to its list. The language is the team's where it is known, else
the browser's, else English. Under `/api` the answer stays JSON.

### API

The same paths under `/api`, mirroring the pages (never typed, so no need to
shorten further). Each read returns the state shaped for the token's role.

- `GET /api/contacts`: `[{name, lang, contact: {url, whatsapp}}]`, the teams
  with a `contact`; `url` is the team's `/chat/<team id>` path and `whatsapp`
  says whether the contact is a WhatsApp link (so the button can look like one).
  No token, and not counted by the limit. The `team` object of the other reads
  has the same `contact` when set.
- `GET /api/<tok>`: `{role, team, matches}`, `matches` being a list, by date, of
  `{n, date, opponent, location, status, sets_won, current_set|null, current_score|null}`;
  `current_set` and `current_score` are the number and the score of the set in
  play while the match is `live`.
- `GET /api/<tok>/<m>`: state of the match.
- `GET /api/<tok>/<m>/stream`: server-sent events, its data the state as JSON
  (`event: state`). A state is sent on connect and whenever it differs from the
  last one sent: after a write, a reload of the team file, or time passing (the
  window opens, the match locks). When nothing changed in 20 seconds, a comment
  line (`: keepalive`) is sent instead, which also keeps the connection open.
  The stream ends if the team file drops the match. The browser's `EventSource`
  reconnects by itself. The match list is fetched again every 30 seconds; the
  family match page fetches the state every 15 seconds beside its stream, and
  the staff screen, which has none, every 10, saying when that fails (`?poll=N`
  sets the seconds).

Staff token only:

- `POST /api/<tok>/<m>/events`: body `{set, type, team?}`; creates an event.
  `201`, body is the new staff state (an auto-closing `set_end` included).
- `PATCH /api/<tok>/<m>/events/<id>`: body `{team}` on a timeout. `200`, new
  state. An id or a set that is not a number is `422 invalid_event`, like one
  that does not exist.
- `DELETE /api/<tok>/<m>/events/<id>`: a point or a timeout. `200`, new state.
- `POST /api/<tok>/<m>/sets/<s>/reopen`: `200`, new state.
- `POST /api/<tok>/<m>/reset`: deletes the whole log of the match, at any time;
  `200`, new state. `404` unless the team is a `demo` (temporary, with it).

Undo is a `DELETE` of the latest point or timeout of the viewed set, chosen by
the client from `events`.

The same paths with another kind of token (see *Tokens*): with the team's coach
token, `<tok>` below (see *Editing*):

- `GET /api/<tok>`: `{team, matches: [{n, date, opponent}]}`, by date.
- `GET /api/<tok>/season`:
  `{team, matches: [{n, date, opponent, location, closed, has_log}]}`, by date;
  `closed` is true from the opening of the match's window, `has_log` when events
  were recorded.
- `PUT /api/<tok>/season`: body
  `{matches: [{n?, date, opponent, location: {name, address?}}]}`, the whole
  list: a match with an `n` the team has keeps its rules and takes the new date,
  opponent and place; one without gets the next number never used; one left out
  is removed. `200`, the same body as the `GET`. `409 match_closed` for a change
  to a match whose window has opened, or to a date whose window would already be
  open; `422 invalid_event` with the validator's text otherwise, for example
  when a removed match has a log.
- `GET /api/<tok>/team`:
  `{id, name, lang, timezone, colors: {us, them}, contact, rules, languages}`.
- `PUT /api/<tok>/team`: the same fields but `id` and `languages`; the matches
  and `demo` are left as they are, and an empty `contact` is removed from the
  file. `200`, the same body as the `GET`. `422 invalid_event` for an invalid
  value.

The admin, with the admin token:

- `GET /api/<tok>`:
  `{teams: [{id, name, links: {family, staff, coach}}], languages}`; the links
  are paths.
- `POST /api/<tok>`: body
  `{id, name, lang, timezone, colors: {us, them}, contact?, rules}`; creates the
  team with no matches. `201`, the team as the coach's `GET` shows it.
  `422 invalid_event` for an invalid value, an id in use, or an id whose links
  would move another team's.

### Editing

Who may do what, by the token they hold:

- **Staff**, anyone the bench trusts: score the match in play, and nothing else.
- **The coach or DA** of a team (coach token), once the admin has created it:
  the season and the team's settings, both below.
- **The admin** (one for the server): create a team and hand out the links of
  all.

What the coach changes:

- **The season** (date, opponent and place of each match), before the matches
  are played: a match is read-only once its window has opened, a new match or a
  new date cannot be at a time already open for scoring, and a match is removed
  only when nothing was recorded for it.
- **The team** (name, language, time zone, colors, contact, rules), with its
  matches left alone.

A save is validated exactly as at startup, including the refusal to drop a match
a log refers to; if it fails nothing is written and the answer says why. When it
passes, the previous file is copied to `<team>.json.bak`, the new one is written
atomically in the layout of the repository's formatter (two-space indent, keys
in a fixed order, no escaped non-ASCII), the new configuration is in force at
once and the streams of the team's matches get a `state`. A new team's links are
the ones its id gives; it is refused if that would move those of a team already
there.

### Errors

Status `404` (unknown token or match), `409` (the request breaks a rule), `422`
(the request is malformed), `429` (too many unknown tokens from one address, see
*Tokens*), `500` (an error of the server, logged in `logs/app.log` with its
traceback). Body `{"error": code, "detail": text}`, with `code` among
`set_not_started`, `set_closed`, `set_already_started`, `previous_set_open`,
`match_over`, `not_open_yet`, `locked`, `timeout_limit`, `protected_event`,
`invalid_event`, `not_found`, `rate_limited`, `server_error`, and for the
editing pages `match_closed`. `invalid_event` (also an event id that does not
exist, or a set that is not closed when reopening, or a value the editing pages
refuse) comes with `422`; the other rule codes, and `match_closed`, with `409`.

## Strings

The keys are those of `i18n.json`, which is their one list: one for each text a
page shows, and `err_<code>` for each error code of the contract. A value may
hold `{0}`, replaced by an argument (the set number, a time). A key missing from
a language falls back to `en`; an `err_<code>` missing from both is shown as the
server's `detail`. Names, dates and numbers come from data and `Intl`, in the
page's language. `tests/test_i18n.py` checks that every language has the same
keys, that every key a script names exists and every other key is used, and that
every error code has a message.
