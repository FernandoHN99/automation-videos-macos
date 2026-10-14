import { baseName, bytes, clamp, clock, percent } from "../core/format.js";

/** Modelo puro da linha do tempo: trechos (prontos, atual, fila), posições em % e marcas de hora. */

const MIN_WEIGHT = 1; // s: arquivos sem estimativa ainda aparecem como um traço
const UNKNOWN_CURRENT = 60; // s: duração assumida do vídeo atual enquanto não há ritmo medido
const MAX_DONE = 150;
const MAX_QUEUED = 300;
const TICK_STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 14400];
const MAX_TICKS = 8;

export const KINDS = {
  done: "done",
  current: "current",
  queued: "queued",
  material: "material",
};

/** @returns {{ kind: string, weight: number, label: string, progress?: number }[]} */
export function buildSegments(status) {
  const segments = [];

  for (const job of (status.session ?? []).slice(-MAX_DONE)) {
    const saved = job.percent > 0.05 ? `, −${percent(job.percent)}` : "";
    segments.push({ kind: KINDS.done, weight: Math.max(job.t || 0, MIN_WEIGHT), label: `${baseName(job.rel)}: pronto${saved}` });
  }

  const current = status.current;
  if (current) {
    const progress = clamp(current.percent || 0, 0, 99);
    const total = current.remaining && progress > 0 ? current.remaining / (1 - progress / 100) : current.est || UNKNOWN_CURRENT;
    segments.push({
      kind: KINDS.current,
      weight: Math.max(total, MIN_WEIGHT),
      progress,
      label: `${baseName(current.rel)}: ${percent(progress)}`,
    });
  }

  for (const file of status.queue.slice(0, MAX_QUEUED)) {
    segments.push({
      kind: file.video ? KINDS.queued : KINDS.material,
      weight: file.est || MIN_WEIGHT,
      label: `${baseName(file.rel)} (${bytes(file.size)})`,
    });
  }
  return segments;
}

/**
 * Posição de cada trecho em % da faixa, a do "agora" (fim dos prontos mais o avanço do atual) e o total em segundos.
 * @returns {{ spans: { left: number, width: number }[], total: number, elapsed: number, nowPercent: number }}
 */
export function layout(segments) {
  const total = segments.reduce((sum, segment) => sum + segment.weight, 0) || 1;
  let cursor = 0;
  let elapsed = 0;
  const spans = segments.map(segment => {
    const span = { left: (cursor / total) * 100, width: (segment.weight / total) * 100 };
    if (segment.kind === KINDS.done) elapsed = cursor + segment.weight;
    if (segment.kind === KINDS.current) elapsed = cursor + (segment.weight * segment.progress) / 100;
    cursor += segment.weight;
    return span;
  });
  return { spans, total, elapsed, nowPercent: (elapsed / total) * 100 };
}

/** Marcas de hora cheia (ou meia hora, 15 min...) ao longo da faixa, no ritmo que couber. */
export function ruler({ total, elapsed }, nowEpoch) {
  const start = nowEpoch - elapsed;
  const step = TICK_STEPS.find(candidate => total / candidate <= MAX_TICKS) ?? TICK_STEPS.at(-1);
  const ticks = [];
  for (let at = Math.ceil(start / step) * step; at <= start + total; at += step) {
    ticks.push({ left: ((at - start) / total) * 100, label: clock(at) });
  }
  return ticks;
}
