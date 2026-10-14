/** Descrição dos campos do formulário de compressão e regras de quando cada um aparece. */

/**
 * k: chave em /api/settings · t: tipo do controle · sw: só software · hw: só hardware (VideoToolbox) · h: ajuda
 * @type {{ k: string, t: "select"|"range"|"number", l: string, h: string, min?: number, max?: number, sw?: boolean, hw?: boolean }[]}
 */
export const FIELDS = [
  { k: "codec", t: "select", l: "Codec de vídeo", h: "H.264 toca em qualquer lugar. HEVC é menor, mas players e aparelhos antigos podem não reproduzir. VideoToolbox usa o chip do Mac: bem mais rápido, arquivo maior." },
  { k: "crf", t: "range", l: "Qualidade (CRF)", min: 0, max: 51, sw: true, h: "Menor = melhor qualidade e arquivo maior. 18 é quase sem perdas, 23 é o padrão, 28 é bem compacto." },
  { k: "vt_quality", t: "range", l: "Qualidade (1 a 100)", min: 1, max: 100, hw: true, h: "Maior = melhor qualidade e arquivo maior." },
  { k: "preset", t: "select", l: "Velocidade (preset)", sw: true, h: "Mais lento = arquivo menor para a mesma qualidade. O padrão é medium." },
  { k: "tune", t: "select", l: "Ajuste fino (tune)", sw: true, h: "Otimiza para um tipo de conteúdo. Deixe em “nenhum” se não tiver certeza." },
  { k: "profile", t: "select", l: "Perfil H.264", sw: true, h: "“automático” deixa o x264 escolher (high). baseline e main servem para aparelhos muito antigos." },
  { k: "max_height", t: "select", l: "Resolução máxima", h: "Só reduz, nunca amplia. “Original” mantém a resolução de cada vídeo." },
  { k: "max_fps", t: "select", l: "Quadros por segundo (máx.)", h: "Só reduz. “Original” mantém." },
  { k: "audio", t: "select", l: "Áudio", h: "Automático copia o áudio sem perda quando já é AAC." },
  { k: "audio_bitrate", t: "select", l: "Taxa do áudio AAC", h: "Usada quando o áudio é convertido." },
  { k: "min_savings", t: "range", l: "Economia mínima", min: 0, max: 90, h: "Se a compressão economizar menos que isso, o original é copiado sem mudanças." },
  { k: "threads", t: "number", l: "Threads (0 = automático)", min: 0, max: 16, h: "Quantos núcleos o encoder usa. Automático costuma ser o melhor." },
  { k: "nice", t: "range", l: "Prioridade (nice)", min: 0, max: 19, h: "Maior = o Mac fica mais responsivo para outros apps e a conversão pode demorar mais. 0 = sem limite." },
];

export const CHECKS = [
  { k: "faststart", l: "Faststart (começa a tocar antes de carregar tudo)" },
  { k: "copy_metadata", l: "Copiar metadados e capítulos" },
];

export const NUMERIC_KEYS = new Set(["crf", "vt_quality", "max_height", "max_fps", "min_savings", "threads", "nice"]);

export const LABELS = {
  max_height: value => (value ? `${value}p` : "Original"),
  max_fps: value => (value ? `${value} fps` : "Original"),
  audio: { auto: "Automático (copia AAC)", aac: "Sempre converter para AAC", none: "Remover áudio" },
  codec: {
    libx264: "H.264 (libx264), o padrão",
    libx265: "H.265/HEVC (libx265)",
    h264_videotoolbox: "H.264 por hardware (VideoToolbox)",
    hevc_videotoolbox: "H.265 por hardware (VideoToolbox)",
  },
};

export const isHardwareCodec = codec => codec.endsWith("_videotoolbox");

/** O campo aparece para esta combinação de ajustes? */
export function isFieldVisible(field, settings, options) {
  const hardware = isHardwareCodec(settings.codec);
  if (field.sw && hardware) return false;
  if (field.hw && !hardware) return false;
  if (field.k === "profile") return Boolean(options.profiles[settings.codec]?.length);
  if (field.k === "tune") return Boolean(options.tunes[settings.codec]?.length);
  if (field.k === "audio_bitrate") return settings.audio !== "none";
  return true;
}

/** Texto ao lado do rótulo (valor atual dos controles deslizantes e numéricos). */
export function valueText(field, value) {
  if (field.t === "select") return "";
  return field.k === "min_savings" ? `${value}%` : String(value);
}
