// The admin's page: the teams with the links to hand out, and the form that
// creates a team. Reached with the admin token, which is for the whole server;
// a team is changed afterwards by its coach.

import {
  ApiError,
  DEFAULT_LANGUAGE,
  h,
  loadStrings,
  markRole,
  show,
  t,
} from "./common.js";
import { teamForm } from "./teamform.js";

const root = document.getElementById("app");
root.classList.add("wide"); // these pages assume a tablet or a wider screen
const [token, which] = location.pathname.split("/").filter(Boolean);
const path = `/api/${token}`;

async function call(options = {}) {
  const response = await fetch(path, {
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

function listView(data) {
  show(
    root,
    h("h1", {}, t("admin")),
    h(
      "div",
      { class: "cards" },
      ...data.teams.map((team) =>
        h(
          "div",
          { class: "card" },
          h("b", {}, `${team.name} `, h("small", {}, team.id)),
          h(
            "p",
            { class: "links" },
            ...["family", "staff", "coach"].flatMap((role) => [
              h("a", { href: team.links[role] }, t(`link_${role}`)),
              " ",
            ]),
          ),
        ),
      ),
    ),
    h(
      "a",
      { role: "button", class: "add", href: `/${token}/new` },
      t("admin_new"),
    ),
  );
}

try {
  await loadStrings(DEFAULT_LANGUAGE);
  markRole("staff", "admin");
  document.title = `Staff · ${t("admin")}`;
  const overview = await call();
  if (which !== "new") {
    listView(overview);
  } else {
    const code = DEFAULT_LANGUAGE;
    teamForm({
      root,
      isNew: true,
      languages: overview.languages,
      backHref: `/${token}`,
      backLabel: t("admin"),
      team: {
        id: "",
        name: "",
        lang: overview.languages.includes(code) ? code : overview.languages[0],
        timezone: "Europe/Rome",
        colors: { us: "#1d6fd8", them: "#c2410c" },
        contact: "",
        rules: {
          best_of: 5,
          points: 25,
          last_set_points: 15,
          win_by: 2,
          timeouts_per_set: 2,
        },
      },
      submit: async (body) => {
        await call({ method: "POST", body });
        location.assign(`/${token}`);
        return null;
      },
    });
  }
} catch (error) {
  show(root, h("p", { class: "notice" }, error.message));
}
