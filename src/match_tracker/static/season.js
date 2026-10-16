// The season of a team: its matches, with date, opponent and place, to add, change
// and remove. For the coach or DA. A match is changed before it is played: once
// its window has opened it is read-only, and only one with no record is removed.

import {
  ApiError,
  applyColors,
  describeInvalid,
  h,
  icon,
  loadStrings,
  markRole,
  setTitle,
  show,
  t,
} from "./common.js";

const root = document.getElementById("app");
root.classList.add("wide"); // these pages assume a tablet or a wider screen
const token = location.pathname.split("/").filter(Boolean)[0];
const path = `/api/${token}/season`;

async function call(options) {
  const response = await fetch(path, {
    ...options,
    headers: options?.body ? { "content-type": "application/json" } : {},
    body: options?.body ? JSON.stringify(options.body) : undefined,
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

const copy = (m) => ({ ...m, location: { ...m.location } });

function start(data) {
  let matches = data.matches.map(copy);
  let notice = null;
  let busy = false;

  function field(label, value, onchange, locked, type = "text") {
    return h(
      "label",
      {},
      label,
      h("input", {
        type,
        value: value ?? "",
        disabled: locked || busy,
        oninput: (event) => onchange(event.target.value),
      }),
    );
  }

  function card(match, index) {
    const locked = match.closed === true;
    return h(
      "div",
      { class: "card" },
      locked && h("p", { class: "meta" }, t("match_closed_note")),
      field(
        t("season_date"),
        match.date,
        (v) => (match.date = v),
        locked,
        "datetime-local",
      ),
      field(
        t("season_opponent"),
        match.opponent,
        (v) => (match.opponent = v),
        locked,
      ),
      field(
        t("season_place"),
        match.location.name,
        (v) => (match.location.name = v),
        locked,
      ),
      field(
        t("season_address"),
        match.location.address,
        (v) => (match.location.address = v),
        locked,
      ),
      !match.has_log &&
        h(
          "button",
          {
            class: "icon remove",
            title: t("remove"),
            "aria-label": t("remove"),
            disabled: busy,
            onclick: () => {
              matches.splice(index, 1);
              notice = null;
              render();
            },
          },
          icon("trash3"),
        ),
    );
  }

  async function save() {
    busy = true;
    notice = null;
    render();
    try {
      const body = {
        matches: matches.map(({ n, date, opponent, location }) => ({
          n,
          date,
          opponent,
          location,
        })),
      };
      const saved = await call({ method: "PUT", body });
      matches = saved.matches.map(copy);
      notice = { text: t("saved"), ok: true };
    } catch (error) {
      notice = { text: describeInvalid(error), ok: false };
    }
    busy = false;
    render();
  }

  function render() {
    show(
      root,
      h("p", {}, h("a", { href: `/${token}` }, `‹ ${data.team.name}`)),
      h("h1", {}, `${t("season")} · ${data.team.name}`),
      h("div", { class: "cards" }, ...matches.map(card)),
      h(
        "button",
        {
          class: "icon add",
          title: t("season_add"),
          "aria-label": t("season_add"),
          disabled: busy,
          onclick: () => {
            matches.push({
              date: "",
              opponent: "",
              location: { name: "", address: "" },
            });
            notice = null;
            render();
          },
        },
        icon("calendar-plus"),
      ),
      notice &&
        h("p", { class: notice.ok ? "notice" : "notice error" }, notice.text),
      h(
        "button",
        {
          class: "icon save",
          title: t("save"),
          "aria-label": t("save"),
          disabled: busy,
          onclick: save,
        },
        icon("floppy"),
      ),
    );
  }

  render();
}

try {
  const data = await call();
  await loadStrings(data.team.lang);
  applyColors(data.team);
  markRole("staff");
  setTitle(`${t("season")} · ${data.team.name}`);
  start(data);
} catch (error) {
  show(root, h("p", { class: "notice" }, error.message));
}
