const hasFiles = event => event.dataTransfer?.types?.includes("Files");

/**
 * Liga a janela inteira como área de soltar. Sempre cancela o `drop`: senão o navegador abriria o arquivo.
 * Com o processamento rodando (`isLocked`), nada é aceito e `onBlocked` avisa.
 */
export function watchFileDrops({ isLocked, onDrop, onBlocked, onDragging }) {
  let depth = 0; // dragenter/dragleave disparam também ao cruzar elementos filhos

  addEventListener("dragenter", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (isLocked()) return;
    depth++;
    onDragging(true);
  });

  addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) onDragging(false);
  });

  addEventListener("dragover", event => {
    if (hasFiles(event)) event.preventDefault();
  });

  addEventListener("drop", event => {
    event.preventDefault();
    depth = 0;
    onDragging(false);
    if (!event.dataTransfer?.files?.length) return;
    if (isLocked()) onBlocked();
    else onDrop(event.dataTransfer);
  });
}
