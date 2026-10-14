import { h } from "../core/dom.js";
import { baseName } from "../core/format.js";

/**
 * Linha de arquivo das listas. `side` traz o valor principal e uma nota; `error` (opcional) vai em uma segunda linha.
 * `tone`: "live" destaca o arquivo em andamento.
 */
export function FileRow({ path, where, primary, note, tone, error }) {
  return h(
    "li",
    { class: "file-row", dataset: { tone } },
    h("div", { class: "file-row__main" }, h("span", { class: "file-row__title", title: path }, baseName(path)), where && h("span", { class: "file-row__where" }, where)),
    h("div", { class: "file-row__side" }, h("span", { class: "file-row__primary figure" }, primary), note && h("span", { class: "file-row__note" }, note)),
    error && h("p", { class: "file-row__error" }, error),
  );
}

/** Linha final das listas cortadas: "e mais N arquivos". */
export function MoreRow(count) {
  return h("li", { class: "file-row file-row--more" }, `e mais ${count} ${count === 1 ? "arquivo" : "arquivos"}`);
}
