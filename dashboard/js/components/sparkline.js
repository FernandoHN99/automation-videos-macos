const NS = "http://www.w3.org/2000/svg";
const WIDTH = 100;
const HEIGHT = 32;
const HEADROOM = 1.15;

const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
};

let gradients = 0;

/** Degradê da área sob a linha: colorido no topo e transparente na base (as cores ficam no CSS). Cada traço tem o seu id. */
function createFade() {
  const id = `sparkline-fade-${++gradients}`;
  const gradient = svgEl("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 });
  gradient.append(svgEl("stop", { offset: 0, class: "sparkline__stop-top" }), svgEl("stop", { offset: 1, class: "sparkline__stop-base" }));
  const defs = svgEl("defs");
  defs.append(gradient);
  return { id, defs };
}

/** Traço das últimas `capacity` amostras, sempre esticado na largura toda (as mais antigas saem pela esquerda). */
export function Sparkline({ label, capacity = 60 }) {
  const fade = createFade();
  const area = svgEl("path", { class: "sparkline__area", fill: `url(#${fade.id})` });
  const line = svgEl("path", { class: "sparkline__line", "vector-effect": "non-scaling-stroke" });
  const el = svgEl("svg", { class: "sparkline", viewBox: `0 0 ${WIDTH} ${HEIGHT}`, preserveAspectRatio: "none", role: "img", "aria-label": label });
  el.append(fade.defs, area, line);
  let samples = [];

  function draw() {
    if (samples.length < 2) {
      line.removeAttribute("d");
      area.removeAttribute("d");
      return;
    }
    const peak = Math.max(...samples) * HEADROOM;
    const step = WIDTH / (samples.length - 1);
    const points = samples.map((value, i) => {
      const x = i * step;
      const y = HEIGHT - 1 - (value / peak) * (HEIGHT - 2);
      return `${x.toFixed(2)} ${y.toFixed(2)}`;
    });
    const path = `M${points.join(" L")}`;
    line.setAttribute("d", path);
    area.setAttribute("d", `${path} L${WIDTH} ${HEIGHT} L0 ${HEIGHT} Z`);
  }

  return {
    el,
    push(value) {
      samples = [...samples, value].slice(-capacity);
      draw();
    },
    reset() {
      samples = [];
      draw();
    },
  };
}
