/** Cria elementos sem innerHTML: textos entram como nós de texto, então nome de arquivo nenhum vira HTML. */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  applyProps(el, props);
  el.append(...children.flat(Infinity).filter(isRenderable));
  return el;
}

const isRenderable = child => child != null && child !== false;

function applyProps(el, props) {
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") applyDataset(el, value);
    else if (key === "style") applyStyle(el, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key in el) el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
}

function applyDataset(el, dataset) {
  for (const [name, value] of Object.entries(dataset)) {
    if (value != null) el.dataset[name] = value;
  }
}

function applyStyle(el, style) {
  if (typeof style === "string") el.style.cssText = style;
  else for (const [name, value] of Object.entries(style)) el.style.setProperty(name, value);
}

/** Troca o conteúdo de `el` por `children`. */
export function replace(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter(isRenderable));
}
