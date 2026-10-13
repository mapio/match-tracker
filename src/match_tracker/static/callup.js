// The players called up for a match: a grid of the roster to switch on and off,
// saved in one go. For the coach or DA; open until shortly before the match.

import { ApiError, applyColors, currentLanguage, describe, h, icon, loadStrings, markRole, setTitle, show, t } from "./common.js";

const root = document.getElementById("app");
root.classList.add("wide"); // these pages assume a tablet or a wider screen
const [token, matchNumber] = location.pathname.split("/").filter(Boolean);
const path = `/api/${token}/${matchNumber}/callup`;

async function call(options = {}) {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: options.body ? { "content-type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, answer.error ?? "error", answer.detail ?? response.statusText);
  return answer;
}

function start(data) {
  let selected = new Set(data.players);
  let open = data.open;
  let notice = null;
  let busy = false;
  const until = new Intl.DateTimeFormat(currentLanguage(), { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(data.closes_at));

  async function save() {
    busy = true;
    notice = null;
    render();
    try {
      const saved = await call({ method: "PUT", body: { players: [...selected] } });
      selected = new Set(saved.players);
      open = saved.open;
      notice = { text: t("saved"), ok: true };
    } catch (error) {
      notice = { text: describe(error), ok: false };
      if (error instanceof ApiError && error.code === "callup_closed") open = false;
    }
    busy = false;
    render();
  }

  function render() {
    const buttons = Object.entries(data.roster).map(([id, name]) =>
      h(
        "button",
        {
          class: selected.has(id) ? "pick on" : "pick",
          disabled: !open || busy,
          "aria-pressed": selected.has(id),
          onclick: () => {
            if (selected.has(id)) selected.delete(id);
            else selected.add(id);
            notice = null;
            render();
          },
        },
        name,
      ),
    );
    show(
      root,
      h("p", {}, h("a", { href: `/${token}` }, `‹ ${data.team.name}`)),
      h("h1", {}, `${t("callup")} · ${data.match.opponent}`),
      h("p", { class: "meta" }, open ? t("callup_until", until) : t("callup_closed_note")),
      h("div", { class: "picks" }, ...buttons),
      h("p", { class: "meta" }, t("callup_count", selected.size)),
      notice && h("p", { class: notice.ok ? "notice" : "notice error" }, notice.text),
      open && h("button", { class: "icon save", title: t("save"), "aria-label": t("save"), disabled: busy, onclick: save }, icon("floppy")),
    );
  }

  render();
}

try {
  const data = await call();
  await loadStrings(data.team.lang);
  applyColors(data.team);
  markRole("staff");
  setTitle(`${t("callup")} · ${data.match.opponent}`);
  start(data);
} catch (error) {
  show(root, h("p", { class: "notice" }, error.message));
}
