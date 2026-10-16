// The coach's (or DA's) start page, and the form of the team's settings. Reached
// with the team's coach token: from here the call-up of each match, the season
// and the team's rules, colors and roster are set.

import {
  ApiError,
  applyColors,
  formatDate,
  h,
  icon,
  loadStrings,
  markRole,
  setTitle,
  show,
  t,
} from "./common.js";
import { teamForm } from "./teamform.js";

const root = document.getElementById("app");
root.classList.add("wide"); // these pages assume a tablet or a wider screen
const [token, which] = location.pathname.split("/").filter(Boolean);
const path = `/api/${token}`;

async function call(suffix = "", options = {}) {
  const response = await fetch(path + suffix, {
    method: options.method ?? "GET",
    headers: options.body ? { "content-type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      response.status,
      answer.error ?? "error",
      answer.detail ?? response.statusText,
    );
  return answer;
}

function dashboard(data) {
  show(
    root,
    h("h1", {}, data.team.name),
    h(
      "p",
      { class: "links" },
      h("a", { role: "button", href: `/${token}/season` }, t("season")),
      " ",
      h("a", { role: "button", href: `/${token}/team` }, t("coach_team")),
    ),
    h("h2", {}, t("callup")),
    h(
      "div",
      { class: "cards" },
      ...data.matches.map((match) =>
        h(
          "div",
          { class: "card" },
          h("b", {}, match.opponent),
          h(
            "div",
            { class: "meta" },
            `${formatDate(match.date)} · ${t("callup_count", match.called)}`,
          ),
          match.callup_open
            ? h(
                "a",
                { class: "callup", href: `/${token}/${match.n}/players` },
                [icon("people"), ` ${t("callup")}`],
              )
            : h("div", { class: "meta" }, t("callup_closed_note")),
        ),
      ),
    ),
  );
}

try {
  const data = await call();
  await loadStrings(data.team.lang);
  applyColors(data.team);
  markRole("staff");
  setTitle(`${t("coach")} · ${data.team.name}`);
  if (which !== "team") {
    dashboard(data);
  } else {
    const team = await call("/team");
    teamForm({
      root,
      isNew: false,
      languages: team.languages,
      backHref: `/${token}`,
      backLabel: team.name,
      team: { ...team, contact: team.contact ?? "" },
      submit: (body) => call("/team", { method: "PUT", body }),
    });
  }
} catch (error) {
  show(root, h("p", { class: "notice" }, error.message));
}
