// The form of a team's information: the admin creates a team with it and the
// coach changes theirs. The caller says where it goes back to and what saving
// does.

import { describeInvalid, h, icon, show, t } from "./common.js";

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

  // two columns on a wide screen: the team and its rules
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
    const body = {
      name: team.name,
      lang: team.lang,
      timezone: team.timezone,
      colors: team.colors,
      contact: team.contact || null,
      rules: team.rules,
    };
    try {
      const saved = await submit(isNew ? { id: team.id, ...body } : body);
      if (!saved) return; // the page is being left
      Object.assign(team, saved);
      notice = { text: t("saved"), ok: true };
    } catch (error) {
      notice = { text: describeInvalid(error), ok: false };
    }
    busy = false;
    render();
  }

  render();
}
