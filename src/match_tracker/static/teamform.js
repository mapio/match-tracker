// The form of a team's information, roster included: the admin creates a team
// with it and the coach changes theirs. The caller says where it goes back to and
// what saving does.

import { describeInvalid, h, icon, show, t } from "./common.js";

// a player's identifier: the number after the highest one in the roster, as
// text, so that the logs say nothing of her without the roster
function identifier(taken) {
  const numbers = [...taken].map(Number).filter(Number.isInteger);
  return String(Math.max(0, ...numbers) + 1);
}

// submit(body) saves; it gives the saved team, or nothing when it leaves the page
export function teamForm({
  root,
  team,
  isNew,
  languages,
  backHref,
  backLabel,
  submit,
}) {
  // the roster as rows; a player already in the file keeps her identifier
  let rows = Object.entries(team.roster).map(([id, name]) => ({ id, name }));
  let notice = null;
  let busy = false;

  const input = (label, value, onchange, type = "text", extra = {}) =>
    h(
      "label",
      {},
      label,
      h("input", {
        type,
        value: value ?? "",
        disabled: busy,
        oninput: (e) => onchange(e.target.value),
        ...extra,
      }),
    );

  // three columns on a wide screen: the team, its rules, its players
  function render() {
    const general = [
      isNew && input(t("admin_id"), team.id, (v) => (team.id = v)),
      input(t("admin_name"), team.name, (v) => (team.name = v)),
      h(
        "label",
        {},
        t("admin_lang"),
        h(
          "select",
          { disabled: busy, onchange: (e) => (team.lang = e.target.value) },
          ...languages.map((code) =>
            h("option", { value: code, selected: code === team.lang }, code),
          ),
        ),
      ),
      input(t("admin_timezone"), team.timezone, (v) => (team.timezone = v)),
      h(
        "div",
        { class: "colors" },
        input(
          t("admin_color_us"),
          team.colors.us,
          (v) => (team.colors.us = v),
          "color",
        ),
        input(
          t("admin_color_them"),
          team.colors.them,
          (v) => (team.colors.them = v),
          "color",
        ),
      ),
      input(t("admin_contact"), team.contact, (v) => (team.contact = v)),
      h(
        "label",
        { class: "check" },
        h("input", {
          type: "checkbox",
          checked: team.show_scorers,
          disabled: busy,
          onchange: (e) => (team.show_scorers = e.target.checked),
        }),
        t("admin_show_scorers"),
      ),
    ];
    const rules = [
      h("h2", {}, t("admin_rules")),
      ...Object.keys(team.rules).map((key) =>
        input(
          t(`rule_${key}`),
          team.rules[key],
          (v) => (team.rules[key] = v === "" ? "" : Number(v)),
          "number",
          { min: 1 },
        ),
      ),
    ];
    const players = [
      h("h2", {}, t("admin_roster")),
      h(
        "div",
        { class: "player-list" },
        ...rows.map((row, index) =>
          h(
            "div",
            { class: "player" },
            h("input", {
              type: "text",
              value: row.name,
              placeholder: t("admin_player_name"),
              "aria-label": t("admin_player_name"),
              disabled: busy,
              oninput: (e) => (row.name = e.target.value),
            }),
            h(
              "button",
              {
                class: "icon remove",
                title: t("remove"),
                "aria-label": t("remove"),
                disabled: busy,
                onclick: () => {
                  rows.splice(index, 1);
                  render();
                },
              },
              icon("trash3"),
            ),
          ),
        ),
      ),
      h(
        "button",
        {
          class: "icon add",
          title: t("admin_player_add"),
          "aria-label": t("admin_player_add"),
          disabled: busy,
          onclick: () => {
            rows.push({ id: null, name: "" });
            render();
          },
        },
        icon("person-plus"),
      ),
    ];
    show(
      root,
      h("p", {}, h("a", { href: backHref }, `‹ ${backLabel}`)),
      h("h1", {}, isNew ? t("admin_new") : team.name),
      h(
        "div",
        { class: "columns" },
        h("section", {}, ...general),
        h("section", {}, ...rules),
      ),
      h("section", { class: "roster" }, ...players),
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

  async function save() {
    busy = true;
    notice = null;
    render();
    const taken = new Set(rows.filter((r) => r.id).map((r) => r.id));
    const roster = {};
    for (const row of rows) {
      const name = row.name.trim();
      if (!name) continue;
      row.id = row.id ?? identifier(taken);
      taken.add(row.id);
      roster[row.id] = name;
    }
    const body = {
      name: team.name,
      lang: team.lang,
      timezone: team.timezone,
      colors: team.colors,
      contact: team.contact || null,
      show_scorers: team.show_scorers,
      rules: team.rules,
      roster,
    };
    try {
      const saved = await submit(isNew ? { id: team.id, ...body } : body);
      if (!saved) return; // the page is being left
      Object.assign(team, saved);
      rows = Object.entries(saved.roster).map(([id, name]) => ({ id, name }));
      notice = { text: t("saved"), ok: true };
    } catch (error) {
      notice = { text: describeInvalid(error), ok: false };
    }
    busy = false;
    render();
  }

  render();
}
