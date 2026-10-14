/** Formatação de números, tamanhos, durações e caminhos para a interface (pt-BR). */

const NBSP = " ";
const UNITS = ["B", "KB", "MB", "GB", "TB"];
const decimals = [0, 1, 2].map(
  digits => new Intl.NumberFormat("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits }),
);

/** Número com `digits` casas, vírgula decimal. */
export const decimal = (value, digits = 0) => decimals[digits].format(value);

/** Percentual já com o sinal: 35,7%. */
export const percent = (value, digits = 0) => `${decimal(value, digits)}%`;

export function bytes(value) {
  let n = Math.abs(Number(value) || 0);
  let unit = 0;
  while (n >= 1024 && unit < UNITS.length - 1) {
    n /= 1024;
    unit++;
  }
  const digits = unit === 0 ? 0 : n < 10 ? 2 : 1;
  const sign = Number(value) < 0 ? "−" : "";
  return `${sign}${decimal(n, digits)}${NBSP}${UNITS[unit]}`;
}

/** Duração em segundos: "55 s", "12 min", "2 h 05 min". */
export function duration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "–";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return `${hours}${NBSP}h${NBSP}${String(minutes).padStart(2, "0")}${NBSP}min`;
  if (minutes) return `${minutes}${NBSP}min`;
  return `${total}${NBSP}s`;
}

/** Hora do dia (HH:MM) de um instante em segundos desde a época. */
export const clock = epochSeconds =>
  new Date(epochSeconds * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

export const plural = (n, one, many) => (n === 1 ? one : many);

export const baseName = path => path.split("/").pop();

export const dirName = path => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");

/** Últimas três pastas do caminho, para caber em uma linha. */
export function tailDir(path) {
  const parts = dirName(path).split("/");
  return parts.length > 3 ? `…/${parts.slice(-3).join("/")}` : parts.join("/");
}

export const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
