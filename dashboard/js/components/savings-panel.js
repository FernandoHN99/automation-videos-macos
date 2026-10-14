import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { tween } from "../core/tween.js";
import { bytes, decimal, percent, plural } from "../core/format.js";

function facts({ done, forecast }) {
  return [
    done.kept > 0 && `${done.kept} ${plural(done.kept, "vídeo ficou como estava", "vídeos ficaram como estavam")} (economia baixa)`,
    done.copied > 0 && `${done.copied} ${plural(done.copied, "material copiado", "materiais copiados")} sem mudanças`,
    forecast.bytes > 0 && `Na fila há ${bytes(forecast.bytes)} em vídeos, com previsão de economizar ≈ ${bytes(forecast.saved)} (${percent(forecast.percent)})`,
  ].filter(Boolean);
}

/** Total economizado desde o início: valor, taxa e a barra depois/antes com a parte economizada hachurada. */
export function SavingsPanel() {
  const figure = h("span", { class: "savings__figure" }, "0 B");
  const rate = h("span", { class: "savings__rate figure" });
  const after = h("i", { class: "savings__after" });
  const bar = h("div", { class: "savings__bar", role: "img" }, after);
  const afterCaption = h("span");
  const beforeCaption = h("span");
  const list = h("ul", { class: "savings__facts" });
  const meta = h("span", { class: "panel__meta" });
  const content = h(
    "div",
    { class: "savings__content" },
    h("p", { class: "savings__headline" }, figure, rate),
    bar,
    h("p", { class: "savings__captions figure" }, afterCaption, beforeCaption),
    list,
  );
  const empty = h("p", { class: "empty" }, "Nada concluído ainda. A economia aparece depois do primeiro vídeo.");
  const el = h(
    "section",
    { class: "panel savings", "aria-label": "Economia total" },
    h("header", { class: "panel__head" }, h("h2", { class: "panel__title" }, "Economia total"), meta),
    h("div", { class: "panel__body" }, content, empty),
  );

  const update = watch(
    s => [s.status],
    ([status]) => {
      if (!status) return;
      const { done } = status;
      content.hidden = !done.count;
      empty.hidden = done.count > 0;
      meta.textContent = done.count ? `${done.count} ${plural(done.count, "arquivo", "arquivos")}` : "";
      if (!done.count) return;

      tween(figure, done.saved, bytes, 900);
      rate.textContent = `−${decimal(done.percent, 1)}%`;
      after.style.width = `${(100 * done.output) / done.original}%`;
      bar.setAttribute("aria-label", `Depois: ${bytes(done.output)}. Antes: ${bytes(done.original)}`);
      afterCaption.textContent = `Depois ${bytes(done.output)}`;
      beforeCaption.textContent = `Antes ${bytes(done.original)}`;
      replace(list, facts(status).map(text => h("li", null, text)));
    },
  );

  return { el, update };
}
