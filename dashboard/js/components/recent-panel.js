import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { baseName, bytes, percent, tailDir } from "../core/format.js";

const DECISION_TEXT = { copy: "copiado", keep_original: "mantido" };

function recentRow(job) {
  const saved = job.percent > 0.05;
  const kept = Math.min(100, (100 * job.output) / Math.max(job.original, 1));
  return h(
    "li",
    { class: "recent__row", dataset: { saved } },
    h("div", { class: "recent__main" }, h("span", { class: "file-row__title", title: job.name }, baseName(job.name)), h("span", { class: "file-row__where" }, tailDir(job.name))),
    h("div", { class: "recent__side" }, h("span", { class: "recent__delta figure" }, saved ? `−${percent(job.percent)}` : DECISION_TEXT[job.decision] ?? "pronto"), h("span", { class: "file-row__note" }, `${bytes(job.output)} de ${bytes(job.original)}`)),
    h("div", { class: "recent__bar", "aria-hidden": "true" }, h("i", { style: { width: `${kept}%` } })),
  );
}

/** Últimos arquivos concluídos, cada um com uma barra do tamanho final em relação ao original. */
export function RecentPanel() {
  const list = h("ol", { class: "recent__list" });
  const empty = h("p", { class: "empty" }, "Nada concluído ainda.");
  const el = h(
    "section",
    { class: "panel recent", "aria-label": "Concluídos recentemente" },
    h("header", { class: "panel__head" }, h("h2", { class: "panel__title" }, "Concluídos")),
    h("div", { class: "panel__body" }, list, empty),
  );

  const update = watch(
    s => [s.status],
    ([status]) => {
      if (!status) return;
      empty.hidden = status.recent.length > 0;
      replace(list, status.recent.map(recentRow));
    },
  );

  return { el, update };
}
