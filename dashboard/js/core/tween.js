const running = new WeakMap();

const prefersReducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Anima o número mostrado em `el` até `to`; `format` transforma o valor em texto. */
export function tween(el, to, format, ms = 700) {
  const previous = running.get(el);
  cancelAnimationFrame(previous?.frame);
  const from = previous?.shown ?? to;
  const entry = { shown: to, frame: 0 };
  running.set(el, entry);

  if (from === to || prefersReducedMotion()) {
    el.textContent = format(to);
    return;
  }
  const startedAt = performance.now();
  const step = now => {
    const k = Math.min(1, Math.max(0, (now - startedAt) / ms)); // o timestamp do quadro pode ser anterior ao início
    entry.shown = from + (to - from) * (1 - (1 - k) ** 3);
    el.textContent = format(entry.shown);
    if (k < 1) entry.frame = requestAnimationFrame(step);
  };
  entry.frame = requestAnimationFrame(step);
}
