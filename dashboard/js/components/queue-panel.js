import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { bytes, clock, duration, percent, tailDir } from "../core/format.js";
import { FileRow, MoreRow } from "./file-row.js";

const MAX_ROWS = 150;

const TABS = [
  { id: "queue", label: "Na fila", empty: "Nenhum arquivo na fila." },
  { id: "waiting", label: "Aguardando", empty: "Nenhum arquivo esperando.", hint: "Arquivos recém-copiados esperam alguns minutos para estabilizar" },
  { id: "failed", label: "Falhas", empty: "Nenhuma falha." },
];

function queueRows(status) {
  const { current, queue, eta, running, now } = status;
  const rows = [];
  if (current) {
    rows.push(FileRow({ path: current.rel, where: tailDir(current.rel), primary: percent(current.percent || 0), note: "em andamento", tone: "live" }));
  }
  let elapsed = current?.remaining ?? 0;
  for (const file of queue.slice(0, MAX_ROWS)) {
    elapsed += file.est ?? 0;
    rows.push(
      FileRow({
        path: file.rel,
        where: tailDir(file.rel) || "pasta raiz",
        primary: file.video ? bytes(file.size) : `${bytes(file.size)} material`,
        note: queueNote(file, { eta, running, ready: clock(now + elapsed) }),
      }),
    );
  }
  const hidden = queue.length - MAX_ROWS;
  return hidden > 0 ? [...rows, MoreRow(hidden)] : rows;
}

function queueNote(file, { eta, running, ready }) {
  if (file.retry_in) return `nova tentativa em ${duration(file.retry_in)}`;
  const length = file.duration ? duration(file.duration) : "";
  if (file.video && running && eta.seconds != null) return `${length ? `${length}, ` : ""}pronto ~${ready}`;
  return length;
}

const waitingRows = status => status.waiting.slice(0, MAX_ROWS).map(file => FileRow({ path: file.rel, where: tailDir(file.rel), primary: bytes(file.size) }));

const failedRows = status => status.failed.map(file => FileRow({ path: file.rel, where: tailDir(file.rel), error: (file.error || "").slice(0, 220) }));

const ROWS = { queue: queueRows, waiting: waitingRows, failed: failedRows };

function totalFor(status, id) {
  if (id === "queue") return status.queue.length + (status.current ? 1 : 0);
  return status[id].length;
}

function etaText({ eta, running, now }, hasWork) {
  if (eta.seconds == null || !hasWork) return "";
  const estimate = eta.approx ? " (estimativa)" : "";
  return running ? `termina às ${clock(now + eta.seconds)}${estimate}` : `levaria cerca de ${duration(eta.seconds)}${estimate}`;
}

/** Arquivos da fila, os que esperam estabilizar e as falhas, em abas dentro do painel. */
export function QueuePanel({ store }) {
  const meta = h("span", { class: "panel__meta" });
  const buttons = TABS.map(tab => {
    const count = h("span", { class: "figure" }, "0");
    const button = h(
      "button",
      { class: "segmented__button", title: tab.hint, "aria-pressed": "false", dataset: { tab: tab.id }, onclick: () => store.set({ queueTab: tab.id }) },
      tab.label,
      count,
    );
    return { tab, button, count };
  });
  const list = h("ol", { class: "queue__list" });
  const empty = h("p", { class: "empty" });
  const el = h(
    "section",
    { class: "panel queue", id: "queue-panel", "aria-label": "Fila de arquivos" },
    h("header", { class: "panel__head" }, h("h2", { class: "panel__title" }, "Fila"), meta),
    h("div", { class: "segmented", role: "group", "aria-label": "Mostrar" }, buttons.map(item => item.button)),
    h("div", { class: "panel__body" }, list, empty),
  );

  const update = watch(
    s => [s.status, s.queueTab],
    ([status, activeTab]) => {
      if (!status) return;
      for (const { tab, button, count } of buttons) {
        const total = totalFor(status, tab.id);
        count.textContent = String(total);
        button.setAttribute("aria-pressed", String(tab.id === activeTab));
        button.dataset.alert = tab.id === "failed" && total > 0;
      }
      const rows = ROWS[activeTab](status);
      replace(list, rows);
      empty.hidden = rows.length > 0;
      empty.textContent = TABS.find(tab => tab.id === activeTab).empty;
      meta.textContent = activeTab === "queue" ? etaText(status, rows.length > 0) : "";
    },
  );

  return { el, update };
}
