import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { createIcon } from "../core/icons.js";
import { bytes, plural } from "../core/format.js";

const MAX_GROUPS = 60;
const DEFAULT_SAVING = 0.35; // enquanto não há histórico suficiente

/** Fração que os vídeos devem perder, pelo ritmo dos já concluídos. */
function videoSaving(status) {
  const rate = status?.forecast.percent ?? 0;
  return status?.done.count > 3 && rate > 0 ? rate / 100 : DEFAULT_SAVING;
}

function groupRow(group, uploading, onRemove) {
  const detail = `${group.count > 1 ? `${group.count} arquivos, ` : ""}${bytes(group.bytes)}`;
  return h(
    "li",
    { class: "stage__row", dataset: { failed: group.failed } },
    createIcon(group.isFolder ? "folder" : "file"),
    h("span", { class: "stage__name", title: group.name }, group.isFolder ? `${group.name}/` : group.name),
    h("span", { class: "stage__size figure" }, detail),
    !uploading &&
      h("button", { class: "btn btn--quiet btn--icon", "aria-label": `Remover ${group.name}`, onclick: () => onRemove(group.name) }, createIcon("close")),
  );
}

/** Arquivos escolhidos: lista por grupo, resumo com a economia prevista e o envio com progresso. */
export function HeroStaged({ actions, pickers }) {
  const list = h("ul", { class: "stage__list" });
  const summary = h("p", { class: "stage__summary" });
  const fill = h("i");
  const progressName = h("span", { class: "stage__progress-name" });
  const progressInfo = h("span", { class: "figure" });
  const progress = h(
    "div",
    { class: "stage__progress", hidden: true },
    h("div", { class: "progress", role: "progressbar", "aria-label": "Envio dos arquivos", "aria-valuemin": 0, "aria-valuemax": 100 }, fill),
    h("div", { class: "stage__progress-info" }, progressName, progressInfo),
  );
  const clear = h("button", { class: "btn btn--quiet", onclick: actions.clearOrCancel }, "Limpar");
  const send = h("button", { class: "btn btn--primary", onclick: actions.sendAndStart }, "Enviar e iniciar");
  const add = h(
    "div",
    { class: "stage__add" },
    h("button", { class: "btn btn--quiet btn--sm", onclick: pickers.chooseFiles }, createIcon("file"), "Adicionar arquivos"),
    h("button", { class: "btn btn--quiet btn--sm", onclick: pickers.chooseFolder }, createIcon("folder"), "Adicionar pasta"),
  );
  const el = h(
    "div",
    { class: "stage" },
    h("header", { class: "stage__head" }, h("h2", { class: "stage__title" }, "Pronto para enviar"), add),
    list,
    summary,
    progress,
    h("div", { class: "stage__actions" }, clear, send),
  );

  const paintList = watch(
    s => [s.staging, Boolean(s.upload)],
    ([staging, uploading]) => {
      const groups = staging.groups.slice(0, MAX_GROUPS).map(group => groupRow(group, uploading, actions.removeGroup));
      const hidden = staging.groups.length - MAX_GROUPS;
      replace(list, groups, hidden > 0 && h("li", { class: "stage__row" }, h("span", { class: "stage__name" }, `e mais ${hidden} itens`)));
      add.hidden = uploading;
    },
  );

  const paintSummary = watch(
    s => [s.staging, s.status],
    ([staging, status]) => {
      const remaining = staging.videoBytes * (1 - videoSaving(status));
      replace(
        summary,
        h("b", { class: "figure" }, bytes(staging.bytes)),
        ` em ${staging.count} ${plural(staging.count, "arquivo", "arquivos")}`,
        staging.videoBytes > 0 && [". Os vídeos devem cair para ≈ ", h("b", { class: "figure" }, bytes(remaining))],
        staging.ignored > 0 && [". ", h("span", { class: "is-warn" }, `${staging.ignored} ${plural(staging.ignored, "arquivo oculto ignorado", "arquivos ocultos ignorados")}`)],
      );
    },
  );

  const paintUpload = watch(
    s => [s.upload],
    ([upload]) => {
      progress.hidden = !upload;
      send.disabled = Boolean(upload);
      send.textContent = upload ? "Enviando…" : "Enviar e iniciar";
      clear.textContent = upload ? "Cancelar envio" : "Limpar";
      if (!upload) {
        fill.style.width = "0";
        return;
      }
      const share = (100 * upload.loaded) / Math.max(upload.total, 1);
      fill.style.width = `${share}%`;
      fill.parentElement.setAttribute("aria-valuenow", share.toFixed(0));
      progressName.textContent = upload.name;
      progressInfo.textContent = `${bytes(upload.loaded)} de ${bytes(upload.total)}, ${bytes(upload.speed)}/s`;
    },
  );

  return {
    el,
    update(state) {
      paintList(state);
      paintSummary(state);
      paintUpload(state);
    },
  };
}
