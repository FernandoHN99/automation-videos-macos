import { h } from "../core/dom.js";

/** Caixa de confirmação modal. `confirm()` devolve uma promessa com true (confirmou) ou false (cancelou ou Esc). */
export function ConfirmDialog() {
  const title = h("h2", { id: "confirm-title" });
  const text = h("p", { class: "modal__text" });
  const no = h("button", { class: "btn" }, "Cancelar");
  const yes = h("button", { class: "btn" });
  const el = h("dialog", { class: "modal", "aria-labelledby": "confirm-title" }, title, text, h("div", { class: "modal__actions" }, no, yes));

  function confirm({ title: heading, text: body, yes: label = "Confirmar", danger = false }) {
    return new Promise(resolve => {
      title.textContent = heading;
      text.textContent = body;
      yes.textContent = label;
      yes.className = `btn ${danger ? "btn--danger" : "btn--primary"}`;
      const finish = answer => {
        el.close();
        resolve(answer);
      };
      yes.onclick = () => finish(true);
      no.onclick = () => finish(false);
      el.oncancel = () => resolve(false);
      el.showModal();
    });
  }

  return { el, confirm };
}
