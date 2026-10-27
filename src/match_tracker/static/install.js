// How to put the app on a phone's home screen: the same for everyone, so no token.

import { DEFAULT_LANGUAGE, h, loadStrings, show, t } from "./common.js";

await loadStrings(DEFAULT_LANGUAGE);
document.title = t("install_title");
show(
  document.getElementById("app"),
  h("h1", {}, t("install_title")),
  h("h2", {}, t("install_android")),
  h(
    "ol",
    {},
    h("li", {}, t("install_android_1")),
    h("li", {}, t("install_android_2")),
    h("li", {}, t("install_android_3")),
  ),
  h("h2", {}, t("install_iphone")),
  h(
    "ol",
    {},
    h("li", {}, t("install_iphone_1")),
    h("li", {}, t("install_iphone_2")),
    h("li", {}, t("install_iphone_3")),
  ),
  h("p", {}, t("install_note")),
);
