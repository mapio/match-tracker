// The chart of the points of a match: for each set a line per team, the time in
// minutes from the start of the set against the points, one set at a time or
// all of them. The page draws what the state holds, and again when it changes.

import {
  api,
  applyColors,
  h,
  loadStrings,
  markRole,
  matchNumber,
  poll,
  footLinks,
  setTitle,
  show,
  t,
  token,
  watch,
} from "./common.js";

const NS = "http://www.w3.org/2000/svg";
const ALL = "all";
const POLL_SECONDS = 15;
const MARGIN = { left: 30, right: 34, top: 8, bottom: 24 };

const svg = (tag, attrs = {}, text) => {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attrs))
    node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
};

const minutes = (ms) => ms / 60000;
const clock = (m) =>
  `${Math.floor(m)}'${String(Math.round((m % 1) * 60) % 60).padStart(2, "0")}`;

// the points and timeouts of a set as {m, team, type} with the time in
// minutes from its start; the staff have the whole log, the families a timeline
function setEvents(state, set) {
  const log = state.events ?? state.timeline;
  const start = Date.parse(state.sets.find((s) => s.set === set).started_at);
  return log
    .filter((e) => e.set === set && ["point", "timeout"].includes(e.type))
    .map((e) => ({ ...e, m: minutes(Date.parse(e.t) - start) }));
}

export async function start(initial, root) {
  let state = initial;
  let chosen = null;
  let following = true; // on the set in play, until a set is picked in the menu
  let bubble = null;

  const lastSet = () => state.sets.at(-1)?.set;
  const shown = () => {
    const sets = state.sets.map((s) => s.set);
    if (chosen === ALL) return sets;
    return sets.includes(chosen) ? [chosen] : sets.slice(-1);
  };

  function tip(target, point, team) {
    show(
      bubble,
      h("b", {}, `${point.us}–${point.them}`),
      ` · ${clock(point.m)}`,
    );
    bubble.style.borderColor = `var(--${team})`;
    bubble.hidden = false;
    const box = target.getBoundingClientRect();
    const left = box.left + box.width / 2 - bubble.offsetWidth / 2;
    const room = innerWidth - bubble.offsetWidth - 4;
    bubble.style.left = `${Math.min(Math.max(left, 4), room)}px`;
    bubble.style.top = `${Math.max(box.top - bubble.offsetHeight - 6, 4)}px`;
  }

  function setChart(set, width, height) {
    const events = setEvents(state, set);
    const rules = state.rules;
    const score = { us: 0, them: 0 };
    const lines = { us: [{ m: 0, p: 0 }], them: [{ m: 0, p: 0 }] };
    const timeouts = [];
    let last = 0;
    for (const e of events) {
      if (e.type === "timeout") {
        timeouts.push(e);
        continue;
      }
      score[e.team] += 1;
      lines[e.team].push({
        m: e.m,
        p: score[e.team],
        us: score.us,
        them: score.them,
      });
      last = e.m;
    }
    const target = set === rules.best_of ? rules.last_set_points : rules.points;
    const top = Math.max(target, score.us, score.them);
    const span = Math.max(10, Math.ceil(last / 5) * 5);
    const x = (m) =>
      MARGIN.left + (m / span) * (width - MARGIN.left - MARGIN.right);
    const y = (p) =>
      MARGIN.top + (1 - p / top) * (height - MARGIN.top - MARGIN.bottom);
    const chart = svg("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": `${t("set")} ${set}`,
    });
    for (let p = 0; p <= top; p += 5) {
      chart.append(
        svg("line", {
          class: "grid",
          x1: MARGIN.left,
          x2: width - MARGIN.right,
          y1: y(p),
          y2: y(p),
        }),
      );
      chart.append(
        svg(
          "text",
          {
            class: "tick",
            x: MARGIN.left - 5,
            y: y(p) + 4,
            "text-anchor": "end",
          },
          p,
        ),
      );
    }
    for (let m = 0; m <= span; m += 5)
      chart.append(
        svg(
          "text",
          { class: "tick", x: x(m), y: height - 6, "text-anchor": "middle" },
          m,
        ),
      );
    for (const e of timeouts)
      chart.append(
        svg("line", {
          class: `timeout ${e.team}`,
          x1: x(e.m),
          x2: x(e.m),
          y1: MARGIN.top,
          y2: height - MARGIN.bottom,
        }),
      );
    for (const team of ["them", "us"]) {
      const path = lines[team].map(
        (d, i) => `${i ? "L" : "M"}${x(d.m).toFixed(1)},${y(d.p).toFixed(1)}`,
      );
      chart.append(svg("path", { class: `line ${team}`, d: path.join(" ") }));
      for (const point of lines[team].slice(1)) {
        chart.append(
          svg("circle", {
            class: `dot ${team}`,
            cx: x(point.m),
            cy: y(point.p),
            r: 2.4,
          }),
        );
        // a larger target, for a finger
        const hit = svg("circle", {
          class: "hit",
          cx: x(point.m),
          cy: y(point.p),
          r: 11,
        });
        hit.addEventListener("click", (event) => {
          event.stopPropagation();
          tip(hit, point, team);
        });
        chart.append(hit);
      }
      const end = lines[team].at(-1);
      chart.append(
        svg("circle", {
          class: `end ${team}`,
          cx: x(end.m),
          cy: y(end.p),
          r: 3.5,
        }),
      );
    }
    // the final scores at the right of the lines, kept apart when they are close
    const ends = ["us", "them"]
      .map((team) => ({ team, y: y(score[team]) }))
      .sort((a, b) => a.y - b.y);
    if (ends[1].y - ends[0].y < 14) ends[1].y = ends[0].y + 14;
    for (const e of ends)
      chart.append(
        svg(
          "text",
          { class: `final ${e.team}`, x: width - MARGIN.right + 5, y: e.y + 4 },
          score[e.team],
        ),
      );
    return h(
      "section",
      {},
      h(
        "h2",
        {},
        h("span", {}, `${t("set")} ${set}`),
        h(
          "b",
          {},
          h("span", { class: "us" }, score.us),
          " – ",
          h("span", { class: "them" }, score.them),
        ),
      ),
      chart,
    );
  }

  function drawCharts() {
    const width = Math.max(340, root.clientWidth - 24);
    // one set takes the height left under the title and the menu, less the
    // heading of the set and a margin
    const left = innerHeight - charts.getBoundingClientRect().top - 60;
    const height =
      chosen === ALL ? (width > 600 ? 190 : 210) : Math.max(160, left);
    show(charts, ...shown().map((n) => setChart(n, width, height)));
  }

  // the title and the menu of the sets are drawn again only when a set appears:
  // the points that come in meanwhile leave an open menu alone
  function drawFrame(sets) {
    show(
      frame,
      h(
        "a",
        { class: "back", href: `/${token}/${matchNumber}` },
        `‹ ${t("match_page")}`,
      ),
      h("h1", {}, t("chart")),
      h("a", { class: "privacy-top", href: "/privacy" }, t("privacy")),
      h("p", { class: "sub" }, `${state.team.name} – ${state.match.opponent}`),
      h(
        "div",
        { class: "key" },
        h("span", { class: "us" }, state.team.name),
        h("span", { class: "them" }, state.match.opponent),
      ),
      sets.length
        ? h(
            "select",
            {
              "aria-label": t("set"),
              onchange: (event) => {
                following = false;
                chosen =
                  event.target.value === ALL ? ALL : Number(event.target.value);
                drawCharts();
              },
            },
            ...sets.map((n) =>
              h(
                "option",
                { value: n, selected: n === chosen },
                `${t("set")} ${n}`,
              ),
            ),
            h(
              "option",
              { value: ALL, selected: chosen === ALL },
              t("all_sets"),
            ),
          )
        : h("p", { class: "notice" }, t("not_started")),
    );
  }

  function render() {
    const sets = state.sets.map((s) => s.set);
    if (following || (chosen !== ALL && !sets.includes(chosen)))
      chosen = lastSet() ?? null;
    setTitle(`${state.team.name} – ${state.match.opponent}`);
    if (sets.join() !== framed) {
      framed = sets.join();
      drawFrame(sets);
    }
    drawCharts();
  }

  const frame = h("div", { class: "frame" });
  const charts = h("div");
  bubble = h("div", { class: "tip", hidden: true });
  let framed = null;
  show(
    root,
    frame,
    charts,
    // turned on its side the page has no room for them: the notice goes and the
    // link joins the title row
    h(
      "div",
      { class: "chart-foot" },
      h("p", { class: "foot" }, t("chart_note")),
      footLinks(),
    ),
    bubble,
  );
  addEventListener("click", () => {
    bubble.hidden = true;
  });
  addEventListener("resize", drawCharts);
  const update = (next) => {
    state = next;
    render();
  };
  watch(`/${matchNumber}/stream`, update, () => {});
  poll(`/${matchNumber}`, POLL_SECONDS, update);
  chosen = lastSet() ?? null;
  render();
}

// the page of a match, for the families and the staff
const root = document.getElementById("app");
try {
  const state = await api(`/${matchNumber}`);
  await loadStrings(state.team.lang);
  applyColors(state.team);
  markRole(state.role);
  await start(state, root);
} catch (error) {
  show(root, h("p", { class: "notice" }, error.message));
}
