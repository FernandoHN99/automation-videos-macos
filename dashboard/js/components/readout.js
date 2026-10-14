import { h } from "../core/dom.js";

/** Leitura de instrumento: rótulo, valor grande em DIN e uma nota curta. */
export function Readout(label) {
  const value = h("span", { class: "readout__value figure" }, "–");
  const note = h("span", { class: "readout__note" });
  const el = h("div", { class: "readout" }, h("span", { class: "readout__label" }, label), value, note);

  function set(text, noteText = "") {
    value.textContent = text;
    note.textContent = noteText;
  }

  return { el, set };
}
