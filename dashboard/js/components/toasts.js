import { h } from "../core/dom.js";

const LIFETIME_MS = 5200;

/** Avisos curtos no canto da tela. `tone`: "ok" ou "bad". */
export function Toasts() {
  const el = h("div", { class: "toasts", "aria-live": "polite" });

  function toast(message, tone = "") {
    const item = h("div", { class: "toast", dataset: { tone } }, message);
    el.append(item);
    setTimeout(() => item.remove(), LIFETIME_MS);
  }

  return { el, toast };
}
