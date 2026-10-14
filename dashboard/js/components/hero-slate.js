import { h } from "../core/dom.js";
import { watch } from "../core/store.js";
import { createIcon } from "../core/icons.js";
import { duration, plural } from "../core/format.js";

const EMPTY_TITLE = "Solte as aulas aqui";
const EMPTY_LEAD = "Vídeos, PDFs e imagens. As pastas e os nomes são mantidos, e nada é apagado.";

/** Claquete sobre a carta de ajuste: convida a soltar arquivos ou, com fila esperando, a iniciá-la. */
export function HeroSlate({ actions, pickers }) {
  const title = h("h2", { class: "slate__title" }, EMPTY_TITLE);
  const lead = h("p", { class: "slate__lead" }, EMPTY_LEAD);
  const start = h("button", { class: "btn btn--primary", hidden: true, onclick: actions.startRun }, "Iniciar fila");
  const files = h("button", { class: "btn", onclick: pickers.chooseFiles }, createIcon("file"), "Escolher arquivos");
  const folder = h("button", { class: "btn", onclick: pickers.chooseFolder }, createIcon("folder"), "Escolher pasta");
  const el = h("div", { class: "slate" }, title, lead, h("div", { class: "slate__actions" }, start, files, folder));

  const update = watch(
    s => [s.status],
    ([status]) => {
      const waiting = status ? status.queue.length + (status.current ? 1 : 0) : 0;
      start.hidden = !waiting;
      title.textContent = waiting ? "A fila está pronta" : EMPTY_TITLE;
      if (!waiting) {
        lead.textContent = EMPTY_LEAD;
        return;
      }
      const work = status.eta.seconds != null ? `, cerca de ${duration(status.eta.seconds)} de trabalho` : "";
      lead.textContent = `${waiting} ${plural(waiting, "arquivo espera", "arquivos esperam")} para começar${work}. Você também pode soltar mais aulas aqui.`;
    },
  );

  return { el, update };
}
