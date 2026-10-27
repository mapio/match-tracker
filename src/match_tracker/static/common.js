// Code shared by the pages: the API, the strings, formatting and the stream.

const parts = location.pathname.split("/").filter(Boolean);
export const token = parts[0];
export const matchNumber = parts[1];

export class ApiError extends Error {
  constructor(status, code, detail) {
    super(detail);
    this.status = status;
    this.code = code;
    this.detail = detail; // the server's own words, for a code with no message of ours
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(`/api/${token}${path}`, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(
      response.status,
      answer.error ?? "error",
      answer.detail ?? response.statusText,
    );
  }
  return answer;
}

let strings = {};
let language = "en";

// ?lang= wins over the team's language, which wins over English.
export async function loadStrings(teamLanguage) {
  strings = await (await fetch("/static/i18n.json")).json();
  const asked = new URLSearchParams(location.search).get("lang");
  language = [asked, teamLanguage, "en"].find((code) => code && strings[code]);
  document.documentElement.lang = language;
}

export const currentLanguage = () => language;

export function t(key, ...args) {
  const text = strings[language]?.[key] ?? strings.en?.[key] ?? key;
  return text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
}

// The date is the wall time of the match, so it is shown as written, not converted.
export function formatDate(local) {
  const [date, time] = local.split("T");
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  return new Intl.DateTimeFormat(language, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(year, month - 1, day, hour, minute));
}

export function mapsUrl(place) {
  const query = encodeURIComponent(place.address ?? place.name);
  return `https://www.google.com/maps/search/?api=1&query=${query}`;
}

// The role goes on the page so that the stylesheet can set the staff pages
// apart (a frame), and the title says it too, for a row of tabs.
export function markRole(role, app = role) {
  document.documentElement.dataset.role = role;
  const [title, suffix] = APPS[app];
  document.querySelector("meta[name=apple-mobile-web-app-title]").content =
    title;
  document.querySelector("link[rel=apple-touch-icon]").href =
    `/icons/icon-192${suffix}.png`;
}

// The short name and icon suffix of each role's app, as the server's manifest says
// (`APPS` in server.py): the pages say them for Safari, which ignores the manifest.
// The coach's and the admin's pages are styled as staff but are apps of their own.
const APPS = {
  family: ["MatchTracker", ""],
  staff: ["MTStaff", "-staff"],
  coach: ["MTCoach", "-coach"],
  admin: ["MTAdmin", "-admin"],
};

export function setTitle(title) {
  const staff = document.documentElement.dataset.role === "staff";
  document.title = staff ? `${t("staff")} · ${title}` : title;
}

export function applyColors(team) {
  document.documentElement.style.setProperty("--us", team.colors.us);
  document.documentElement.style.setProperty("--them", team.colors.them);
}

export const dash = (a, b) => `${a} – ${b}`;

// used and remaining timeouts as dots
export const dots = (used, limit) =>
  "●".repeat(Math.min(used, limit)) + "○".repeat(Math.max(limit - used, 0));

// What to tell a person about a failed request: the message for the server's
// error code, else the server's own text, and for a lost connection "not recorded".
export function describe(error) {
  if (!(error instanceof ApiError)) return t("not_recorded");
  const text = t(`err_${error.code}`);
  return text === `err_${error.code}` ? error.detail : text;
}

// The same for the pages that edit, where "invalid" comes with the validator's own
// words saying which value is wrong.
export const describeInvalid = (error) =>
  error instanceof ApiError && error.code === "invalid_event"
    ? error.detail
    : describe(error);

// Builds an element; children are text or nodes, never HTML.
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (name === "class") node.className = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value === true) node.setAttribute(name, "");
    else if (value !== false && value != null) node.setAttribute(name, value);
  }
  node.append(
    ...children.flat().filter((child) => child != null && child !== false),
  );
  return node;
}

// Replaces what a page holds. Children that are null, undefined or false are
// left out: replaceChildren would write them as the text "null" or "false".
export function show(parent, ...children) {
  parent.replaceChildren(
    ...children.flat().filter((child) => child != null && child !== false),
  );
}

// A safety net beside the stream, and the only update the staff screen gets from
// others: fetches the state every few seconds (?poll=N overrides, for tests) and
// hands it over when it differs from the last one; onStatus(true|false) says if
// the last fetch worked.
export function poll(path, seconds, onState, onStatus = () => {}) {
  const asked = Number(new URLSearchParams(location.search).get("poll"));
  let last = null;
  return setInterval(
    async () => {
      try {
        const state = await api(path);
        onStatus(true);
        const text = JSON.stringify(state);
        if (text !== last) onState(state);
        last = text;
      } catch {
        onStatus(false);
      }
    },
    (asked || seconds) * 1000,
  );
}

// onState gets each state sent; onStatus(true|false) says if the stream is up.
export function watch(path, onState, onStatus) {
  const source = new EventSource(`/api/${token}${path}`);
  source.addEventListener("state", (event) => {
    onStatus(true);
    onState(JSON.parse(event.data));
  });
  source.onopen = () => onStatus(true);
  source.onerror = () => onStatus(false);
  return source;
}

// An icon of the icon font (Bootstrap Icons, loaded by the pages that edit): the
// name is the font's, "trash3" for bi-trash3. Decorative: the button names itself.
export const icon = (name) =>
  h("i", { class: `bi bi-${name}`, "aria-hidden": "true" });

// The language of a page that belongs to no team (the landing, the error pages, the
// admin's): Italian, whatever the browser says; ?lang= still overrides it.
export const DEFAULT_LANGUAGE = "it";

// The links to the privacy notice and to how to install the app, at the foot of
// the public pages (the pages that edit, which their editors know, leave them
// to the landing page).
export const footLinks = () =>
  h(
    "p",
    { class: "foot" },
    h("a", { href: "/privacy" }, t("privacy")),
    " · ",
    h("a", { href: `/install?lang=${language}` }, t("install_link")),
  );

// The manifest is the token's own, so the pages that have a token link it; the
// error pages, where the token may be wrong, and the install page do not.
if (token && !("noManifest" in document.documentElement.dataset)) {
  document.head.append(
    h("link", { rel: "manifest", href: `/${token}/manifest.webmanifest` }),
  );
}
