/**
 * Cliente da API local do painel.
 *
 * Formato de /api/status (resumo): { now, running, current, session[], queue[], waiting[], failed[],
 *   done{count,original,output,saved,percent,kept,copied}, forecast{bytes,saved,percent}, eta{seconds,approx},
 *   recent[], free, disk_total }
 */

const TOKEN = document.querySelector('meta[name="vp-token"]')?.content ?? "";

export const UPLOAD_CANCELLED = "cancelado";

async function request(url, { method = "GET", headers = {}, body } = {}) {
  const writing = method !== "GET";
  const response = await fetch(url, {
    method,
    body,
    cache: "no-store",
    headers: writing ? { "X-VP-Token": TOKEN, ...headers } : headers,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || String(response.status));
  return data;
}

/** Envia um arquivo ({ file, rel }); devolve a promessa e um `abort`. `onBytes` recebe os bytes enviados. */
function upload(item, onBytes) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-VP-Token", TOKEN);
    xhr.setRequestHeader("X-Rel", encodeURIComponent(item.rel));
    xhr.setRequestHeader("X-Mtime", String(item.file.lastModified || 0));
    xhr.upload.onprogress = event => onBytes(event.loaded);
    xhr.onload = () => {
      const body = parseJson(xhr.responseText);
      if (xhr.status === 200) resolve(body);
      else reject(new Error(body.error || String(xhr.status)));
    };
    xhr.onerror = () => reject(new Error("conexão perdida"));
    xhr.onabort = () => reject(new Error(UPLOAD_CANCELLED));
    xhr.send(item.file);
  });
  return { promise, abort: () => xhr.abort() };
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

export const api = {
  status: () => request("/api/status"),
  settings: () => request("/api/settings"),
  saveSettings: payload =>
    request("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  start: () => request("/api/start", { method: "POST" }),
  stop: () => request("/api/stop", { method: "POST" }),
  upload,
};
