// The match list, the same for staff and family tokens.

import { api, applyColors, dash, formatDate, h, loadStrings, mapsUrl, markRole, privacyLink, setTitle, show, t, token } from "./common.js";

const root = document.getElementById("app");
let ready = false;

function result(match) {
  return match.status === "upcoming" ? "—" : dash(match.sets_won.us, match.sets_won.them);
}

function card(match, featured = false) {
  const live = match.status === "live";
  const score = live && match.current_score ? dash(match.current_score.us, match.current_score.them) : null;
  return h(
    "div",
    { class: featured ? "card featured" : "card" },
    h(
      "a",
      { class: "main", href: `/${token}/${match.n}` },
      h(
        "div",
        { class: "row" },
        h("b", {}, h("span", { class: "dot" }), match.opponent, live && h("span", { class: "live" }, t("live"))),
        h("span", { class: "result" }, result(match)),
      ),
      h(
        "div",
        { class: "meta" },
        formatDate(match.date),
        score && ` · ${t("set")} ${match.current_set}: ${score}`,
      ),
    ),
    h("a", { class: "where", href: mapsUrl(match.location), target: "_blank", rel: "noopener", title: t("where") }, `📍 ${match.location.name}`),
  );
}

// The match in play, or else the next one, on top; then the results, newest
// first; then the later matches. The server sends them by date.
function layout(matches) {
  const upcoming = matches.filter((m) => m.status === "upcoming");
  const featured = matches.find((m) => m.status === "live") ?? upcoming[0];
  const results = matches.filter((m) => m.status === "over").reverse();
  const later = upcoming.filter((m) => m !== featured);
  return [
    featured && card(featured, true),
    results.length > 0 && h("h2", {}, t("results")),
    ...results.map((m) => card(m)),
    later.length > 0 && h("h2", {}, t("upcoming")),
    ...later.map((m) => card(m)),
  ];
}

async function refresh() {
  try {
    const list = await api("");
    if (!ready) {
      await loadStrings(list.team.lang);
      applyColors(list.team);
      markRole(list.role);
      setTitle(list.team.name);
      ready = true;
    }
    show(root, h("h1", {}, list.team.name), layout(list.matches), privacyLink());
  } catch (error) {
    if (!ready) show(root, h("p", { class: "notice" }, error.message));
  }
}

await refresh();
setInterval(refresh, 30000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refresh();
});
