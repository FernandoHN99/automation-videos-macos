import { h } from "../core/dom.js";
import { watch } from "../core/store.js";
import { tween } from "../core/tween.js";
import { baseName, bytes, clamp, clock, decimal, duration, tailDir } from "../core/format.js";
import { Readout } from "./readout.js";
import { Sparkline } from "./sparkline.js";

/** Herói enquanto processa: arquivo atual, percentual gigante, leituras e o traço de velocidade. */
export function HeroLive() {
  const name = h("h2", { class: "live__name" });
  const where = h("p", { class: "live__where" });
  const position = h("p", { class: "live__position" });
  const percentValue = h("span", { class: "live__percent-value" }, "0,0");
  const percentBox = h(
    "div",
    { class: "live__percent", role: "progressbar", "aria-label": "Progresso do vídeo atual", "aria-valuemin": 0, "aria-valuemax": 100 },
    percentValue,
    h("span", { class: "live__percent-sign" }, "%"),
  );
  const remaining = Readout("Faltam");
  const speed = Readout("Velocidade");
  const output = Readout("Saída até agora");
  const finish = Readout("Fila termina");
  const trace = Sparkline({ label: "Velocidade de conversão ao longo do tempo" });

  const active = h(
    "div",
    { class: "live__active" },
    h("header", { class: "live__head" }, h("div", { class: "live__title" }, name, where), position),
    percentBox,
    h(
      "div",
      { class: "live__gauges" },
      h("div", { class: "live__readouts" }, remaining.el, speed.el, output.el, finish.el),
      h("div", { class: "live__trace" }, h("span", { class: "live__trace-caption" }, "Velocidade de conversão"), trace.el),
    ),
  );
  const searching = h(
    "div",
    { class: "live__searching" },
    h("h2", null, "Procurando o próximo arquivo"),
    h("p", null, "Os arquivos recém-copiados esperam alguns minutos para estabilizar."),
  );
  const el = h("div", { class: "live" }, active, searching);

  let shownFile = null;

  function paintCurrent(status, current) {
    if (current.rel !== shownFile) {
      shownFile = current.rel;
      name.textContent = baseName(current.rel);
      name.title = current.rel;
      where.textContent = tailDir(current.rel) || "pasta raiz";
    }
    const done = status.session.length;
    position.textContent = `Arquivo ${done + 1} de ${done + 1 + status.queue.length}`;

    const progress = clamp(current.percent || 0, 0, 100);
    percentBox.setAttribute("aria-valuenow", progress.toFixed(1));
    tween(percentValue, progress, value => decimal(value, 1), 900);

    remaining.set(duration(current.remaining), "neste vídeo");
    speed.set(current.speed ? `${decimal(current.speed, 1)}×` : "–", "o tempo real");
    output.set(current.out_size ? bytes(current.out_size) : "–", `de ${bytes(current.size)} de entrada`);

    const { seconds, approx } = status.eta;
    finish.set(seconds != null ? `${approx ? "≈ " : ""}${clock(status.now + seconds)}` : "–", seconds != null ? `daqui a ${duration(seconds)}` : "");
  }

  const update = watch(
    s => [s.status],
    ([status]) => {
      if (!status) return;
      const current = status.current;
      active.hidden = !current;
      searching.hidden = Boolean(current);
      if (!status.running) {
        trace.reset();
        shownFile = null;
      }
      if (!current) return;
      if (current.speed) trace.push(current.speed);
      paintCurrent(status, current);
    },
  );

  return { el, update };
}
