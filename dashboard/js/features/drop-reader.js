/** Lê o que foi solto na janela (arquivos e pastas inteiras) como uma lista plana de { file, rel }. */

function readBatch(reader) {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

async function readAll(directory) {
  const reader = directory.createReader();
  const entries = [];
  for (let batch = await readBatch(reader); batch.length; batch = await readBatch(reader)) entries.push(...batch);
  return entries;
}

async function walk(entry, out) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    out.push({ file, rel: entry.fullPath.replace(/^\//, "") });
  } else if (entry.isDirectory) {
    for (const child of await readAll(entry)) await walk(child, out);
  }
}

/** Precisa ser chamada de forma síncrona dentro do evento `drop`: o DataTransfer expira após o primeiro await. */
export async function readDropped(dataTransfer) {
  const entries = [...dataTransfer.items].filter(item => item.kind === "file").map(item => item.webkitGetAsEntry?.());
  const files = [...dataTransfer.files];
  if (!entries.length || !entries.every(Boolean)) return files.map(file => ({ file, rel: file.name }));

  const out = [];
  for (const entry of entries) await walk(entry, out);
  return out;
}
