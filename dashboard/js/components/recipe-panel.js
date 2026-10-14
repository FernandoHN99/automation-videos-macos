import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { createIcon } from "../core/icons.js";

const WARNING_PREFIX = "Atenção";

/** Cada linha da API vem como "Rótulo: texto"; avisos começam com "Atenção". */
function describe(line) {
  if (line.startsWith(WARNING_PREFIX)) return h("p", { class: "recipe__warning" }, line);
  const split = line.indexOf(":");
  if (split < 1) return h("p", { class: "recipe__line" }, line);
  return h("div", { class: "recipe__line" }, h("dt", null, line.slice(0, split)), h("dd", null, line.slice(split + 1).trim()));
}

/** Resumo, em português, do processo de compressão em uso, com atalho para a gaveta de ajustes. */
export function RecipePanel({ actions }) {
  const badge = h("span", { class: "chip" }, "Padrão");
  const lines = h("dl", { class: "recipe__list" });
  const open = h("button", { class: "btn btn--sm", onclick: actions.openSettings }, createIcon("sliders"), "Ajustar");
  const el = h(
    "section",
    { class: "panel recipe", "aria-label": "Como cada vídeo é comprimido" },
    h("header", { class: "panel__head" }, h("h2", { class: "panel__title" }, "Como comprime"), h("div", { class: "recipe__tools" }, badge, open)),
    h("div", { class: "panel__body" }, lines),
  );

  const update = watch(
    s => [s.settings],
    ([settings]) => {
      if (!settings) return;
      badge.textContent = settings.custom ? "Personalizado" : "Padrão";
      badge.dataset.tone = settings.custom ? "custom" : "";
      el.dataset.custom = settings.custom;
      replace(lines, settings.description.map(describe));
    },
  );

  return { el, update };
}
