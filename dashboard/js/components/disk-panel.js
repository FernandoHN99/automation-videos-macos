import { h } from "../core/dom.js";
import { watch } from "../core/store.js";
import { bytes } from "../core/format.js";

const LOW_DISK_BYTES = 10 * 2 ** 30;

/** Espaço livre no disco da pasta de trabalho; avisa quando sobra pouco. */
export function DiskPanel() {
  const meta = h("span", { class: "panel__meta figure" });
  const used = h("i");
  const gauge = h("div", { class: "disk__gauge", role: "img", hidden: true }, used);
  const el = h(
    "section",
    { class: "panel disk", "aria-label": "Disco" },
    h("header", { class: "panel__head" }, h("h2", { class: "panel__title" }, "Disco"), meta),
    h("div", { class: "disk__body" }, gauge),
  );

  const update = watch(
    s => [s.status],
    ([status]) => {
      if (!status) return;
      const low = status.free < LOW_DISK_BYTES;
      el.dataset.low = low;
      meta.textContent = `${bytes(status.free)} livres`;
      gauge.hidden = !status.disk_total;
      if (!status.disk_total) return;
      const share = (100 * (status.disk_total - status.free)) / status.disk_total;
      used.style.width = `${share}%`;
      gauge.setAttribute("aria-label", `${bytes(status.free)} livres de ${bytes(status.disk_total)}`);
    },
  );

  return { el, update };
}
