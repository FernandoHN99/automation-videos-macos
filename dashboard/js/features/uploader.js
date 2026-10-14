import { UPLOAD_CANCELLED } from "../core/api.js";

/**
 * Envia os arquivos escolhidos, um por vez. Quem chama recebe os avanços e as falhas por callbacks
 * e o resultado final em { sent, failed, cancelled }.
 */
export function createUploader({ api, staging, onProgress, onItemDone, onItemFailed }) {
  let abortCurrent = null;
  let cancelled = false;

  async function run() {
    cancelled = false;
    const items = staging.items;
    const total = items.reduce((sum, item) => sum + item.file.size, 0);
    const startedAt = performance.now();
    let done = 0;
    let sent = 0;
    let failed = 0;

    const report = (item, loaded) => {
      const all = done + loaded;
      onProgress({ name: item.rel, loaded: all, total, speed: all / Math.max((performance.now() - startedAt) / 1000, 0.001) });
    };

    for (const item of items) {
      if (cancelled) break;
      report(item, 0);
      try {
        const request = api.upload(item, loaded => report(item, loaded));
        abortCurrent = request.abort;
        await request.promise;
        sent++;
        staging.remove(item);
        onItemDone(item);
      } catch (error) {
        if (error.message === UPLOAD_CANCELLED) break;
        failed++;
        staging.markFailed(item, error.message);
        onItemFailed(item, error);
      }
      done += item.file.size;
    }
    abortCurrent = null;
    onProgress(null);
    return { sent, failed, cancelled };
  }

  function cancel() {
    cancelled = true;
    abortCurrent?.();
  }

  return { run, cancel };
}
