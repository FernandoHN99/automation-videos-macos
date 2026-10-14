import { h } from "../core/dom.js";

/** Seletores nativos de arquivos e de pasta (campos ocultos, abertos por botões). */
export function createFilePicker(onPick) {
  const emit = (event, relOf) => {
    onPick([...event.target.files].map(file => ({ file, rel: relOf(file) })));
    event.target.value = "";
  };
  const files = h("input", { type: "file", multiple: true, hidden: true, onchange: e => emit(e, file => file.name) });
  const folder = h("input", {
    type: "file",
    webkitdirectory: true,
    hidden: true,
    onchange: e => emit(e, file => file.webkitRelativePath || file.name),
  });
  document.body.append(files, folder);

  return { chooseFiles: () => files.click(), chooseFolder: () => folder.click() };
}
