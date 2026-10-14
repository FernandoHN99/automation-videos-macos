import { plural } from "../core/format.js";
import { createUploader } from "./uploader.js";
import { readDropped } from "./drop-reader.js";

export const LOCKED_MESSAGE = "Pare o processamento (ou aguarde terminar) para adicionar arquivos.";

/** Seleção de arquivos, envio ao servidor e a decisão de iniciar a fila em seguida. */
export function createStagingController({ api, store, staging, ui, startRun }) {
  const publish = () => store.set({ staging: staging.snapshot() });

  const uploader = createUploader({
    api,
    staging,
    onProgress: upload => store.set({ upload }),
    onItemDone: publish,
    onItemFailed: (item, error) => {
      publish();
      ui.toast(`Falhou: ${item.rel} (${error.message})`, "bad");
    },
  });

  function addFiles(list) {
    staging.add(list);
    publish();
  }

  async function addDropped(dataTransfer) {
    addFiles(await readDropped(dataTransfer));
  }

  function removeGroup(name) {
    staging.removeGroup(name);
    publish();
  }

  /** Durante o envio o botão cancela; fora dele, esvazia a seleção. */
  function clearOrCancel() {
    if (store.get().upload) {
      uploader.cancel();
      return;
    }
    staging.clear();
    publish();
  }

  async function sendAndStart() {
    if (store.get().upload) return;
    const { sent, cancelled } = await uploader.run();
    publish();
    if (sent) ui.toast(`${sent} ${plural(sent, "arquivo enviado", "arquivos enviados")}`, "ok");
    if (sent && !cancelled) await startRun();
  }

  return { addFiles, addDropped, removeGroup, clearOrCancel, sendAndStart };
}
