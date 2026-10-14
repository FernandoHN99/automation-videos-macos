import { h } from "../core/dom.js";

const BARS = ["white", "yellow", "cyan", "green", "magenta", "red", "blue"];

/** Sete barras de teste miniaturizadas: a mesma carta que o herói mostra quando está parado. */
export function BrandMark() {
  const bars = BARS.map(name => h("i", { style: { background: `var(--bar-${name})` } }));
  return { el: h("span", { class: "brand-mark", "aria-hidden": "true" }, bars) };
}
