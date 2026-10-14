const NS = "http://www.w3.org/2000/svg";

/* Constantes confiáveis (nunca vêm de dados), por isso podem entrar como markup. */
const PATHS = {
  file: '<path d="M5 2.5h6l4 4v11H5z"/><path d="M11 2.5v4h4"/>',
  folder: '<path d="M2.5 5.5h5l1.5 2h8.5v8h-15z"/>',
  close: '<path d="M5 5l10 10M15 5L5 15"/>',
  sliders: '<path d="M3 6h9M16 6h1M3 14h1M9 14h8"/><circle cx="14" cy="6" r="1.8"/><circle cx="6.5" cy="14" r="1.8"/>',
  copy: '<rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V4.5A1.5 1.5 0 0 0 11.5 3h-6A1.5 1.5 0 0 0 4 4.5v6A1.5 1.5 0 0 0 5.5 12H7"/>',
};

export function createIcon(name) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.6");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = PATHS[name];
  return svg;
}
