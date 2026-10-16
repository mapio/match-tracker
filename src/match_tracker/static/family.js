// The match view for families: the live set, the closed sets, the latest points.

import {
  currentLanguage,
  dash,
  dots,
  formatDate,
  h,
  mapsUrl,
  matchNumber,
  poll,
  privacyLink,
  setTitle,
  show,
  t,
  token,
  watch,
} from "./common.js";

const FEED_LENGTH = 10; // the server sends every point of the set; a phone shows the latest
const POLL_SECONDS = 15;

// hours and minutes on the viewer's clock, in the page's language
const hm = (iso) =>
  new Intl.DateTimeFormat(currentLanguage(), {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));

function scoreBox(state) {
  const { sets, sets_won: won, rules } = state;
  const us = state.team.name;
  const them = state.match.opponent;
  if (state.match.status === "upcoming") {
    return h("div", { class: "box idle" }, t("not_started"));
  }
  const over = state.match.status === "over";
  const current = sets.find((s) => s.set === state.current);
  const score = over ? won : (current?.score ?? { us: 0, them: 0 });
  const limit = rules.timeouts_per_set;
  return h(
    "div",
    { class: over ? "box" : "box live-box" },
    h(
      "div",
      { class: "names" },
      h("span", { class: "us" }, us),
      h("span", { class: "them" }, them),
    ),
    h("div", { class: "big" }, dash(score.us, score.them)),
    h(
      "div",
      { class: "sub" },
      over ? t("final") : `${t("set")} ${state.current}`,
      !over && ` · ${dash(won.us, won.them)}`,
      !over && current && ` · ${t("since", hm(current.started_at))}`,
    ),
    !over &&
      h(
        "div",
        { class: "timeouts" },
        `${dots(current?.timeouts.us ?? 0, limit)}   ${dots(current?.timeouts.them ?? 0, limit)}`,
      ),
  );
}

function setsTable(state) {
  const closed = state.sets.filter((s) => s.closed);
  if (!closed.length) return null;
  return h(
    "div",
    { class: "scroll" },
    h(
      "table",
      {},
      h(
        "tr",
        {},
        h("th"),
        h("th", {}, state.team.name),
        h("th", {}, state.match.opponent),
      ),
      ...closed.map((s) =>
        h(
          "tr",
          {},
          // the rows are in order, so the set's number is not printed: its times are
          h(
            "td",
            {},
            s.ended_at &&
              h(
                "span",
                { class: "pill" },
                `${hm(s.started_at)}–${hm(s.ended_at)}`,
              ),
          ),
          h("td", { class: s.winner === "us" ? "win" : "" }, s.score.us),
          h("td", { class: s.winner === "them" ? "win" : "" }, s.score.them),
        ),
      ),
    ),
  );
}

// the viewer's clock in the page's language, from the event's UTC instant;
// seconds, as points can come less than a minute apart
const clock = (iso) =>
  new Intl.DateTimeFormat(currentLanguage(), {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));

function feed(state) {
  if (!state.feed || !state.feed.length) return null;
  // the latest points are for a set in play: a closed set is in the table, and a
  // finished match has its result
  const current = state.sets.find((s) => s.set === state.current);
  if (state.match.status === "over" || current?.closed) return null;
  // the team that scored, and for ours the scorer too when there is one
  const who = (point) => {
    if (point.team === "them") return state.match.opponent;
    const scorer = state.players[point.player];
    return scorer ? `${state.team.name} (${scorer})` : state.team.name;
  };
  return h(
    "div",
    {},
    h("h2", {}, t("last_points")),
    h(
      "ul",
      { class: "feed" },
      ...state.feed
        .slice(0, FEED_LENGTH)
        .map((p) =>
          h(
            "li",
            { class: p.team },
            h("span", { class: "who" }, who(p)),
            h("span", { class: "pill" }, clock(p.t)),
          ),
        ),
    ),
  );
}

export function start(initial, root) {
  const notice = h("p", { class: "notice", hidden: true }, t("offline"));
  const body = h("div");
  show(root, notice, body);

  function render(state) {
    const live = state.match.status === "live";
    setTitle(`${state.team.name} – ${state.match.opponent}`);
    show(
      body,
      h("a", { class: "back", href: `/${token}` }, `‹ ${state.team.name}`),
      h(
        "h1",
        {},
        state.match.opponent,
        live && h("span", { class: "live" }, t("live")),
      ),
      h(
        "p",
        { class: "meta" },
        `${formatDate(state.match.date)} · `,
        h(
          "a",
          {
            href: mapsUrl(state.match.location),
            target: "_blank",
            rel: "noopener",
            title: t("where"),
          },
          `📍 ${state.match.location.name}`,
        ),
      ),
      scoreBox(state),
      setsTable(state),
      feed(state),
      privacyLink(),
    );
  }

  render(initial);
  watch(`/${matchNumber}/stream`, render, (up) => {
    notice.hidden = up;
  });
  // if the stream is held up on the way, the page still follows the match
  poll(`/${matchNumber}`, POLL_SECONDS, render);
}
