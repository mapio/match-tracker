// The staff screen: points, timeouts and the set flow, on one phone-sized view.
// The page keeps only the set being viewed and the last answer; every tap is a
// request and the screen is drawn again from what the server answers.

import {
  ApiError,
  api,
  currentLanguage,
  dash,
  describe,
  dots,
  h,
  matchNumber,
  poll,
  footLinks,
  setTitle,
  show,
  t,
  token,
} from "./common.js";

const BASE = `/${matchNumber}`;
const POLL_SECONDS = 10;
const COUNTED = ["point", "timeout"];

export function start(initial, root) {
  let state = initial;
  let view = initial.current;
  let busy = false;
  let menu = false;
  let extras = []; // the buttons of the menu that close a set or the match
  let notice = null;
  let timer = null;
  const us = () => state.team.name;
  const them = () => state.match.opponent;
  const viewed = () => state.sets.find((s) => s.set === view) ?? null;
  const lastCounted = () =>
    [...state.events]
      .reverse()
      .find((e) => e.set === view && COUNTED.includes(e.type));
  root.classList.add("staff");

  // One request at a time: a second tap while one is in flight is ignored.
  async function send(path, options = {}) {
    if (busy) return;
    busy = true;
    notice = null;
    render();
    const wasClosed = viewed()?.closed ?? false;
    try {
      state = await api(path, options);
      navigator.vibrate?.(30);
      if (!wasClosed && viewed()?.closed) view = state.current;
    } catch (error) {
      notice = describe(error);
      if (error instanceof ApiError) state = await api(BASE).catch(() => state);
    }
    view = Math.min(view, state.current);
    busy = false;
    render();
    schedule();
  }

  const add = (body) =>
    send(`${BASE}/events`, { method: "POST", body: { set: view, ...body } });
  const point = (team) => add({ type: "point", team });
  const fix = (id, body) =>
    send(`${BASE}/events/${id}`, { method: "PATCH", body });
  const drop = (id) => send(`${BASE}/events/${id}`, { method: "DELETE" });
  const undo = () => drop(lastCounted().id);
  const reopen = () => send(`${BASE}/sets/${view}/reopen`, { method: "POST" });
  const extra = (action) => {
    menu = false;
    action();
  };

  async function refresh() {
    state = await api(BASE).catch(() => state);
    view = Math.min(view, state.current);
    render();
    schedule();
  }

  // What others record shows up by itself, and a lost connection is said, since
  // a tap would fail; a tap in flight is not disturbed.
  poll(
    BASE,
    POLL_SECONDS,
    (next) => {
      if (busy) return;
      state = next;
      view = Math.min(view, state.current);
      render();
      schedule();
    },
    (up) => {
      const lost = t("offline");
      if (up && notice === lost) notice = null;
      else if (!up && !busy) notice = lost;
      else return;
      render();
    },
  );

  // The screen unlocks, or locks, by itself when the window opens or closes.
  function schedule() {
    clearTimeout(timer);
    const now = Date.now();
    const next = [state.match.opens_at, state.match.locks_at]
      .map(Date.parse)
      .filter((ms) => ms > now)
      .sort((a, b) => a - b)[0];
    if (next)
      timer = setTimeout(refresh, Math.min(next - now + 500, 2 ** 31 - 1));
  }

  async function keepAwake() {
    try {
      await navigator.wakeLock?.request("screen");
    } catch {
      // not available, or refused: the screen may dim, nothing else changes
    }
  }

  function banner() {
    if (notice) return h("p", { class: "notice error" }, notice);
    if (state.match.editable) return null;
    const opens = Date.parse(state.match.opens_at);
    if (Date.now() < opens) {
      const time = new Intl.DateTimeFormat(currentLanguage(), {
        hour: "2-digit",
        minute: "2-digit",
      }).format(opens);
      return h("p", { class: "notice" }, t("opens_at", time));
    }
    return h("p", { class: "notice" }, t("read_only"));
  }

  function eventRow(event) {
    const clock = new Intl.DateTimeFormat(currentLanguage(), {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    const time = clock.format(new Date(event.t));
    const locked = busy || !state.match.editable;
    const protectedEvent = !COUNTED.includes(event.type);
    const label = {
      set_start: t("started"),
      set_end: t("closed"),
      match_end: t("end_match"),
      point: `${t("point")} · ${event.team === "us" ? us() : them()}`,
      timeout: t("timeout"),
    }[event.type];
    let control = null;
    if (event.type === "timeout" || event.type === "set_start") {
      const counts = state.sets.find((s) => s.set === event.set).timeouts;
      const full = (side) =>
        event.type === "timeout" &&
        side !== event.team &&
        counts[side] >= state.rules.timeouts_per_set;
      control = h(
        "select",
        {
          disabled: locked,
          onchange: (e) => fix(event.id, { team: e.target.value }),
        },
        // a start from before the first server was recorded has none to show
        !event.team &&
          h("option", { value: "", selected: true, disabled: true }, "-"),
        ...[
          ["us", us()],
          ["them", them()],
        ].map(([side, name]) =>
          h(
            "option",
            {
              value: side,
              selected: side === event.team,
              disabled: full(side),
            },
            name,
          ),
        ),
      );
    }
    return h(
      "li",
      {},
      h("span", {}, `${time} · ${label}`),
      control,
      !protectedEvent &&
        h(
          "button",
          { class: "drop", disabled: locked, onclick: () => drop(event.id) },
          t("delete"),
        ),
    );
  }

  // only on a demo team, which the server lets be wiped; to go with the demo
  function resetButton() {
    const reset = () => {
      if (!confirm(t("reset_demo_confirm"))) return;
      menu = false;
      view = 1;
      send(`${BASE}/reset`, { method: "POST" });
    };
    return h("button", { class: "reset", onclick: reset }, t("reset_demo"));
  }

  function menuView() {
    const events = state.events.filter((e) => e.set === view).reverse();
    return h(
      "div",
      { class: "menu" },
      h(
        "header",
        {},
        h("b", {}, `${t("set")} ${view}`),
        h(
          "button",
          {
            onclick: () => {
              menu = false;
              render();
            },
          },
          "✕",
        ),
      ),
      ...[
        banner(),
        h(
          "a",
          { class: "chart-link", href: `/${token}/${matchNumber}/chart` },
          `${t("chart")} ›`,
        ),
        h("div", { class: "extras" }, ...extras),
        state.team.demo && resetButton(),
        h("ul", {}, ...events.map(eventRow)),
        footLinks(),
      ].filter(Boolean),
    );
  }

  function render() {
    const set = viewed();
    const closed = set?.closed ?? false;
    const editable = state.match.editable;
    const over = state.match.status === "over";
    const lastSet = state.sets.at(-1);
    const idle = !editable || busy || !set || closed;
    const score = set?.score ?? { us: 0, them: 0 };
    const used = set?.timeouts ?? { us: 0, them: 0 };
    const limit = state.rules.timeouts_per_set;
    const won = state.sets_won;
    setTitle(`${us()} – ${them()}`);

    const teamRow = (side, name) =>
      h(
        "div",
        { class: "teamrow" },
        // always there, so that the buttons do not move when the serve changes
        h(
          "span",
          {
            class: set?.serving === side ? "ball" : "ball off",
            ...(set?.serving === side && {
              role: "img",
              "aria-label": t("serving"),
            }),
          },
          set?.serving === side ? "🏐" : "",
        ),
        h(
          "button",
          { class: `team ${side}`, disabled: idle, onclick: () => point(side) },
          name,
        ),
        h(
          "button",
          {
            class: `to ${side}`,
            disabled: idle || used[side] >= limit,
            onclick: () => add({ type: "timeout", team: side }),
          },
          t("timeout"),
          h("span", { class: "dots" }, dots(used[side], limit)),
        ),
      );

    // the buttons that start the next set, once there is one to start, by the
    // team that serves first, between the two teams
    const startSet = h(
      "div",
      { class: "starts" },
      ...[
        ["us", us()],
        ["them", them()],
      ].map(([side, name]) =>
        h(
          "button",
          {
            class: `start ${side}`,
            disabled: !editable || busy,
            onclick: () => add({ type: "set_start", team: side }),
          },
          t("serves", name),
        ),
      ),
    );
    const middle = set ? [] : [startSet];

    const bar = h(
      "div",
      { class: "bar" },
      h(
        "button",
        {
          disabled: view <= 1,
          onclick: () => {
            view -= 1;
            render();
          },
        },
        "‹",
      ),
      h(
        "button",
        {
          disabled: view >= state.current,
          onclick: () => {
            view += 1;
            render();
          },
        },
        "›",
      ),
      h(
        "button",
        {
          class: "undo",
          disabled: idle || !lastCounted(),
          onclick: undo,
          title: t("undo"),
          "aria-label": t("undo"),
        },
        "↶",
      ),
      h(
        "button",
        {
          "aria-label": t("menu"),
          onclick: () => {
            menu = true;
            render();
          },
        },
        "⋯",
      ),
    );

    // A set closes by itself at its target and a match is over when a team has
    // won its sets, so these are the rare cases (called off by the referees, or
    // a mistake), kept out of the bar. The match can end from the next set's
    // screen too, which has no set yet: the event names the last set played.
    extras = [
      closed
        ? h(
            "button",
            { disabled: !editable || busy, onclick: () => extra(reopen) },
            t("reopen"),
          )
        : h(
            "button",
            {
              disabled: !editable || busy || !set,
              onclick: () => extra(() => add({ type: "set_end" })),
            },
            t("close_set"),
          ),
      h(
        "button",
        {
          disabled: !editable || busy || !lastSet || over,
          onclick: () =>
            extra(() => add({ type: "match_end", set: lastSet.set })),
        },
        t("end_match"),
      ),
    ];

    show(
      root,
      banner(),
      h(
        "div",
        { class: view === state.current ? "score" : "score past" },
        h("span", {}, `${t("set")} ${view}${closed ? " ✓" : ""}`),
        h("b", {}, dash(score.us, score.them)),
        h("span", {}, `(${won.us}–${won.them})`),
      ),
      teamRow("us", us()),
      ...middle,
      teamRow("them", them()),
      bar,
      menu ? menuView() : null,
    );
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refresh();
      keepAwake();
    }
  });
  render();
  schedule();
  keepAwake();
}
