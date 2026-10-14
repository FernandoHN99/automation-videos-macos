import { h } from "../core/dom.js";
import { watch } from "../core/store.js";
import { TestCard } from "./test-card.js";

/** Véu de tela cheia enquanto arquivos são arrastados sobre a janela: a carta de ajuste acende ("sinal detectado"). */
export function DropOverlay() {
  const el = h("div", { class: "drop-overlay", "aria-hidden": "true" }, TestCard().el, h("p", { class: "drop-overlay__text" }, "Solte para adicionar"));
  const update = watch(
    s => [s.dragging],
    ([dragging]) => el.classList.toggle("is-on", dragging),
  );
  return { el, update };
}
