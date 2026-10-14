import { h } from "../core/dom.js";

/** Carta de ajuste de vídeo (barras SMPTE). As cores e as proporções ficam no CSS. */
export function TestCard() {
  const el = h(
    "div",
    { class: "testcard", "aria-hidden": "true" },
    h("div", { class: "testcard__bars" }),
    h("div", { class: "testcard__strip" }),
    h("div", { class: "testcard__pluge" }),
  );
  return { el };
}
