import { h, replace } from "../core/dom.js";
import { watch } from "../core/store.js";
import { createIcon } from "../core/icons.js";
import { CHECKS, FIELDS, LABELS, NUMERIC_KEYS, isFieldVisible, isHardwareCodec, valueText } from "../features/settings-model.js";

const SAVE_DELAY_MS = 450;
const COMMAND_FLAGS = /((?:-c:v|-crf|-preset|-q:v|-vf|-c:a) \S+)/;

function setOptions(select, items) {
  replace(select, items.map(([value, text]) => h("option", { value }, text)));
}

/** Gaveta com todos os ajustes de compressão. Salva sozinha, pouco depois da última mudança. */
export function SettingsDrawer({ store, actions, ui }) {
  const fields = new Map(); // chave -> { field, wrap, valueEl, input }
  const checks = new Map(); // chave -> checkbox
  let saveTimer = null;

  const currentPayload = () => store.get().settings;

  function collect() {
    const values = {};
    for (const { field, input } of fields.values()) values[field.k] = NUMERIC_KEYS.has(field.k) ? Number(input.value) : input.value;
    for (const [key, input] of checks) values[key] = input.checked;
    return values;
  }

  function fill(values, { options, encoders }) {
    const input = key => fields.get(key).input;
    setOptions(input("codec"), encoders.map(codec => [codec, LABELS.codec[codec] ?? codec]));
    setOptions(input("preset"), options.presets.map(value => [value, value]));
    setOptions(input("tune"), [["", "nenhum"], ...(options.tunes[values.codec] ?? []).map(value => [value, value])]);
    setOptions(input("profile"), [["", "automático"], ...(options.profiles[values.codec] ?? []).map(value => [value, value])]);
    setOptions(input("max_height"), options.max_height.map(value => [value, LABELS.max_height(value)]));
    setOptions(input("max_fps"), options.max_fps.map(value => [value, LABELS.max_fps(value)]));
    setOptions(input("audio"), options.audio.map(value => [value, LABELS.audio[value]]));
    setOptions(input("audio_bitrate"), options.audio_bitrate.map(value => [value, value]));
    for (const { field, wrap, valueEl, input: control } of fields.values()) {
      control.value = values[field.k];
      valueEl.textContent = valueText(field, values[field.k]);
      wrap.hidden = !isFieldVisible(field, values, options);
    }
    for (const [key, input] of checks) input.checked = values[key];
  }

  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => actions.saveSettings({ settings: collect() }), SAVE_DELAY_MS);
  }

  function onEdit(event) {
    const values = collect();
    if (event.target.id === "setting-codec") Object.assign(values, { tune: "", profile: "" }); // as listas dependem do codec
    fill(values, currentPayload());
    scheduleSave();
  }

  function applyProfile(profile) {
    fill({ ...collect(), crf: profile.crf, preset: profile.preset }, currentPayload());
    scheduleSave();
  }

  function buildField(field) {
    const id = `setting-${field.k}`;
    const attrs = { id, min: field.min, max: field.max, oninput: onEdit };
    const input =
      field.t === "select"
        ? h("select", { id, class: "select", oninput: onEdit })
        : h("input", { ...attrs, class: field.t === "range" ? "range" : "number", type: field.t });
    const valueEl = h("b", { class: "field__value figure" });
    const wrap = h(
      "div",
      { class: "field", dataset: { wide: field.k === "codec" } },
      h("label", { htmlFor: id }, h("span", null, field.l), valueEl),
      input,
      h("small", null, field.h),
    );
    fields.set(field.k, { field, wrap, valueEl, input });
    return wrap;
  }

  function buildCheck({ k, l }) {
    const input = h("input", { type: "checkbox", id: `setting-${k}`, oninput: onEdit });
    checks.set(k, input);
    return h("label", { class: "check" }, input, l);
  }

  const chips = h("div", { class: "drawer__chips" });
  const command = h("div", { class: "command" });
  const lockNote = h("p", { class: "drawer__lock", hidden: true }, "Pare o processamento para ajustar. Os valores abaixo ficam só para leitura.");
  const form = h(
    "fieldset",
    { class: "drawer__form" },
    chips,
    h("div", { class: "drawer__fields" }, FIELDS.map(buildField)),
    h("div", { class: "drawer__checks" }, CHECKS.map(buildCheck)),
  );
  const close = h("button", { class: "btn btn--quiet btn--icon", "aria-label": "Fechar ajustes", onclick: actions.closeSettings }, createIcon("close"));
  const el = h(
    "dialog",
    { class: "drawer", "aria-labelledby": "drawer-title" },
    h("header", { class: "drawer__head" }, h("h2", { id: "drawer-title" }, "Ajustes de compressão"), close),
    h(
      "div",
      { class: "drawer__body" },
      h("p", { class: "drawer__lead" }, "Os ajustes valem para os próximos vídeos e ficam salvos. O padrão do config.json nunca é alterado: use “Padrão (atual)” para voltar a ele."),
      lockNote,
      form,
      command,
    ),
  );
  el.addEventListener("close", actions.closeSettings);
  el.addEventListener("click", event => {
    if (event.target === el) actions.closeSettings(); // clique no fundo escurecido
  });

  function paintChips(settings) {
    replace(
      chips,
      h("button", { class: "chip-button", "aria-pressed": String(!settings.custom), onclick: actions.resetSettings }, "Padrão (atual)"),
      Object.entries(settings.profiles).map(([name, profile]) =>
        h("button", { class: "chip-button", "aria-pressed": "false", onclick: () => applyProfile(profile) }, `${name} (CRF ${profile.crf})`),
      ),
    );
  }

  function paintCommand(preview) {
    const copy = h(
      "button",
      { class: "btn btn--sm command__copy", onclick: () => navigator.clipboard.writeText(preview).then(() => ui.toast("Comando copiado", "ok")) },
      createIcon("copy"),
      "Copiar",
    );
    replace(
      command,
      copy,
      h("span", { class: "command__hint" }, "# exemplo: vídeo 1080p30 com áudio AAC"),
      h("br"),
      preview.split(COMMAND_FLAGS).map((part, i) => (i % 2 ? h("b", null, part) : part)),
    );
  }

  /** Não mexe nos controles enquanto o usuário arrasta um deslizante. */
  const isDragging = () => el.contains(document.activeElement) && document.activeElement.type === "range";

  const update = watch(
    s => [s.settingsOpen, s.settings, Boolean(s.status?.running)],
    ([open, settings, running]) => {
      form.disabled = running;
      lockNote.hidden = !running;
      if (settings) {
        paintChips(settings);
        paintCommand(settings.preview);
        if (!isDragging()) fill(settings.settings, settings);
      }
      if (open && !el.open) el.showModal();
      else if (!open && el.open) el.close();
    },
  );

  return { el, update };
}
