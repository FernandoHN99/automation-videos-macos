import { h } from "../core/dom.js";
import { watch } from "../core/store.js";
import { bytes, clock, plural } from "../core/format.js";
import { BrandMark } from "./brand-mark.js";

const LOW_DISK_BYTES = 10 * 2 ** 30;

function statusDetail(status) {
  if (status.running && status.eta.seconds != null) return `A fila termina às ${clock(status.now + status.eta.seconds)}`;
  const waiting = status.queue.length + (status.current ? 1 : 0);
  if (!status.running && waiting) return `${waiting} ${plural(waiting, "arquivo espera", "arquivos esperam")}`;
  return "";
}

/** Barra superior: marca, lâmpada de estado, avisos, relógio e o botão de parar. */
export function Topbar({ actions }) {
  const lamp = h("span", { class: "lamp" });
  const stateText = h("span", { class: "status__state" });
  const detail = h("span", { class: "status__detail" });
  const faults = h("button", { class: "chip", dataset: { tone: "fault" }, hidden: true, onclick: () => actions.showQueueTab("failed") });
  const lowDisk = h("span", { class: "chip", dataset: { tone: "warn" }, hidden: true });
  const time = h("time", { class: "clock figure" });
  const stop = h("button", { class: "btn btn--danger btn--sm", hidden: true, onclick: actions.stopRun }, "Parar");
  const banner = h("p", { class: "banner", role: "alert", hidden: true }, "Sem conexão com o painel. Ele ainda está rodando? Abra de novo com: python3 main.py app");

  const el = h(
    "header",
    { class: "topbar" },
    h(
      "div",
      { class: "topbar__row" },
      h("div", { class: "brand" }, BrandMark().el, "VideoPipeline"),
      h("div", { class: "status", role: "status" }, lamp, stateText, detail),
      h("div", { class: "topbar__end" }, faults, lowDisk, time, stop),
    ),
    banner,
  );

  const tickClock = () => (time.textContent = new Date().toLocaleTimeString("pt-BR"));
  tickClock();
  setInterval(tickClock, 1000);

  const update = watch(
    s => [s.status, s.offline],
    ([status, offline]) => {
      const running = Boolean(status?.running);
      lamp.dataset.state = offline ? "offline" : running ? "live" : "idle";
      stateText.textContent = offline ? "Sem conexão" : running ? "Processando" : "Parado";
      detail.textContent = status && !offline ? statusDetail(status) : "";
      banner.hidden = !offline;
      stop.hidden = !running;

      const failed = status?.failed.length ?? 0;
      faults.hidden = !failed;
      faults.textContent = `${failed} ${plural(failed, "falha", "falhas")}`;

      const lowSpace = status != null && status.free < LOW_DISK_BYTES;
      lowDisk.hidden = !lowSpace;
      if (lowSpace) lowDisk.textContent = `Pouco espaço: ${bytes(status.free)} livres`;
    },
  );

  return { el, update };
}
