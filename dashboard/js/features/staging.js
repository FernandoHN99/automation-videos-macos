const VIDEO = /\.(mp4|mov|mkv|m4v|webm)$/i;

const isHidden = rel => rel.split("/").some(part => part.startsWith("."));
const groupName = rel => rel.split("/")[0];
const keyOf = ({ rel, file }) => `${rel}|${file.size}`;

/** Arquivos escolhidos e ainda não enviados. Pastas viram um grupo só (pelo primeiro nível do caminho). */
export class Staging {
  #items = [];
  #ignored = 0;

  /** @param {{ file: File, rel: string }[]} list */
  add(list) {
    const seen = new Set(this.#items.map(keyOf));
    for (const entry of list) {
      if (isHidden(entry.rel)) {
        this.#ignored++;
        continue;
      }
      if (seen.has(keyOf(entry))) continue;
      seen.add(keyOf(entry));
      this.#items.push({ file: entry.file, rel: entry.rel, error: null });
    }
  }

  get items() {
    return [...this.#items];
  }

  remove(item) {
    this.#items = this.#items.filter(other => other !== item);
    this.#resetIfEmpty();
  }

  removeGroup(name) {
    this.#items = this.#items.filter(item => groupName(item.rel) !== name);
    this.#resetIfEmpty();
  }

  markFailed(item, message) {
    item.error = message;
  }

  clear() {
    this.#items = [];
    this.#resetIfEmpty();
  }

  /** Resumo imutável para a interface. */
  snapshot() {
    const groups = new Map();
    let bytes = 0;
    let videoBytes = 0;
    for (const { file, rel, error } of this.#items) {
      const name = groupName(rel);
      const group = groups.get(name) ?? { name, isFolder: rel.includes("/"), count: 0, bytes: 0, failed: false };
      group.count++;
      group.bytes += file.size;
      group.failed ||= Boolean(error);
      groups.set(name, group);
      bytes += file.size;
      if (VIDEO.test(rel)) videoBytes += file.size;
    }
    return { count: this.#items.length, bytes, videoBytes, ignored: this.#ignored, groups: [...groups.values()] };
  }

  #resetIfEmpty() {
    if (!this.#items.length) this.#ignored = 0;
  }
}

export const EMPTY_STAGING = new Staging().snapshot();
