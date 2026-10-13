// The match page: fetches the state, then hands it to the module of the role.

import { api, applyColors, h, loadStrings, markRole, matchNumber, show } from "./common.js";

const root = document.getElementById("app");

try {
  const state = await api(`/${matchNumber}`);
  await loadStrings(state.team.lang);
  applyColors(state.team);
  markRole(state.role);
  const view = await import(state.role === "staff" ? "./staff.js" : "./family.js");
  view.start(state, root);
} catch (error) {
  show(root, h("p", { class: "notice" }, error.message));
}
