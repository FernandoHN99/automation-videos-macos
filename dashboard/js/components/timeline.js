import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { clock, duration, plural } from "../core/format.js";
import { KINDS, buildSegments, layout, ruler } from "../features/timeline-model.js";

const LEGEND = [
  { kind: KINDS.done, text: n => `${plural(n, "pronto", "prontos")} nesta sessão` },
  { kind: KINDS.current, text: () => "em andamento" },
  { kind: KINDS.queued, text: () => "na fila" },
  { kind: KINDS.material, text: n => plural(n, "material", "materiais") },
];

function legendCounts(status) {
  const queuedVideos = status.queue.filter(file => file.video).length;
  return {
    [KINDS.done]: status.session.length,
    [KINDS.current]: status.current ? 1 : 0,
    [KINDS.queued]: queuedVideos,
    [KINDS.material]: status.queue.length - queuedVideos,
  };
}

function finishHint(status) {
  const { seconds, approx } = status.eta;
  const estimate = approx ? " (estimativa)" : "";
  if (!status.queue.length && !status.current) return "Nenhum arquivo na fila.";
  if (seconds == null) return "";
  return status.running
    ? `A fila inteira termina às ${clock(status.now + seconds)}${estimate}`
    : `A fila levaria cerca de ${duration(seconds)}${estimate}`;
}

/** Sequência da fila como numa ilha de edição: prontos, o vídeo atual com a agulha de "agora" e o que falta, sobre uma régua de horário. */
export function Timeline() {
  const hint = h("p", { class: "timeline__hint" });
  const legend = h("ul", { class: "legend" });
  const ticks = h("div", { class: "timeline__ruler", "aria-hidden": "true" });
  const playhead = h("div", { class: "timeline__playhead", "aria-hidden": "true" }, h("span", { class: "timeline__now" }, "agora"));
  const track = h("div", {
    class: "timeline__track",
    role: "img",
    onpointerover: event => showHint(event.target.closest(".seg")?.dataset.label),
    onpointerleave: () => showHint(),
  });
  const stage = h("div", { class: "timeline__stage" }, ticks, track, playhead);
  const el = h(
    "section",
    { class: "panel timeline", "aria-label": "Linha do tempo da fila" },
    h("header", { class: "panel__head" }, h("h2", { class: "panel__title" }, "Linha do tempo"), legend),
    h("div", { class: "timeline__body" }, hint, stage),
  );

  let defaultHint = "";
  let signature = null;

  function showHint(label) {
    hint.textContent = label ?? defaultHint;
  }

  function rebuildTrack(segments) {
    replace(track, segments.map(segment => h("div", { class: "seg", dataset: { kind: segment.kind } }, segment.kind === KINDS.current && h("i"))));
  }

  function paint(status) {
    const segments = buildSegments(status);
    const geometry = layout(segments);
    const shape = segments.map(segment => segment.kind[0]).join("");
    el.dataset.empty = segments.length === 0;

    if (shape !== signature) {
      signature = shape;
      rebuildTrack(segments);
    }
    segments.forEach((segment, i) => {
      const node = track.children[i];
      const { left, width } = geometry.spans[i];
      node.dataset.label = segment.label;
      node.style.left = `${left}%`;
      node.style.width = `${width}%`;
      if (segment.kind === KINDS.current) node.firstChild.style.width = `${segment.progress}%`;
    });

    playhead.style.left = `${geometry.nowPercent}%`;
    playhead.hidden = !status.running;
    replace(
      ticks,
      ruler(geometry, status.now).map(tick => h("span", { class: "timeline__tick figure", style: { left: `${tick.left}%` } }, tick.label)),
    );
    track.setAttribute("aria-label", finishHint(status) || "Linha do tempo vazia");

    const counts = legendCounts(status);
    replace(
      legend,
      LEGEND.filter(({ kind }) => counts[kind] > 0).map(({ kind, text }) =>
        h("li", { class: "legend__item", dataset: { kind } }, h("span", { class: "legend__count figure" }, String(counts[kind])), text(counts[kind])),
      ),
    );
    defaultHint = finishHint(status);
    showHint();
  }

  const update = watch(
    s => [s.status],
    ([status]) => status && paint(status),
  );

  return { el, update };
}
