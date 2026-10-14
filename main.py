#!/usr/bin/env python3
"""VideoPipeline: comprime vídeos no Mac (FFmpeg) e entrega tudo pronto em my-videos/ready/.

Modo "local" (padrão): incoming/ -> (estável?) -> vídeo: ffprobe -> ffmpeg -> validação; outros arquivos: cópia
verificada -> ready/ (mesma estrutura de pastas) -> original arquivado em originals/. O envio ao microSD é manual.
Modo "upload" (opcional): após a validação, envia ao HP Mini por SSH (verifica UUID/exFAT, SHA-256, publica atômico).

Execução manual: `run` (primeiro plano) ou `start` (segundo plano, sobrevive ao Terminal), com o Mac impedido de
suspender (caffeinate) enquanto houver trabalho. Não há agendamento automático.

Estados: WAITING_STABLE, QUEUED, PROCESSING, COMPLETED, FAILED, SUPERSEDED (+ UPLOAD_PENDING, UPLOADING no modo upload).
Retentativas ficam em `next_attempt` (backoff exponencial) sem estado extra.
Nunca apaga nem sobrescreve originais; nunca sobrescreve arquivo de destino; nunca usa --delete.
"""
import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
import webbrowser
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMP_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".aria2", ".!qb")
BAD_CHARS = re.compile(r'[\x00-\x1f"*:<>?\\|]')
STAGING = ".pipeline-tmp"  # dentro de remote_root, mesmo filesystem do destino final
PROGRESS = "progress.json"  # em state/: progresso do encode atual, lido pelo painel
DEFAULT_EXTS = (".mp4", ".mov", ".mkv", ".m4v", ".webm")


class Transient(Exception):
    """Falha temporária (rede, SSH, microSD ausente): tenta de novo mais tarde."""


class Permanent(Exception):
    """Falha que não se resolve sozinha (arquivo corrompido, ffmpeg falhou)."""


# --------------------------------------------------------------------- utilidades
def load_config(path=None):
    path = Path(path or os.environ.get("VIDEOPIPELINE_CONFIG") or ROOT / "config" / "config.json")
    with open(path) as f:
        return json.load(f)


def safe_part(part):
    """Nome compatível com exFAT/Linux: NFC, sem caracteres proibidos, até 200 bytes."""
    part = BAD_CHARS.sub("_", unicodedata.normalize("NFC", part)).rstrip(" .") or "_"
    while len(part.encode()) > 200:
        stem, dot, ext = part.rpartition(".")
        if dot and len(ext) <= 8:
            part = stem[:-1] + "." + ext
        else:
            part = part[:-1]
    return part


def safe_rel(rel):
    return "/".join(safe_part(p) for p in Path(rel).parts)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_hashed(src, dst):
    """Copia `src` para `dst` (com datas/permissões) calculando o SHA-256 da origem na mesma leitura."""
    h = hashlib.sha256()
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for chunk in iter(lambda: fi.read(4 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
    shutil.copystat(src, dst)
    return h.hexdigest()


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe(path, ffprobe="ffprobe"):
    r = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    if r.returncode != 0:
        raise Permanent(f"ffprobe falhou (arquivo corrompido ou formato não suportado): {r.stderr.strip()[:300]}")
    d = json.loads(r.stdout)
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise Permanent("nenhum stream de vídeo")
    duration = float(d.get("format", {}).get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise Permanent("duração inválida (arquivo incompleto?)")
    num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0
    return {
        "duration": duration, "width": v["width"], "height": v["height"], "fps": round(fps, 3),
        "vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None,
        "bitrate": int(d.get("format", {}).get("bit_rate") or 0),
        "streams": [(s.get("codec_type"), s.get("codec_name")) for s in streams],
    }


# --------------------------------------------------------------------- parâmetros de compressão
CODECS = {"libx264": "h264", "libx265": "hevc", "h264_videotoolbox": "h264", "hevc_videotoolbox": "hevc"}  # encoder -> codec_name no ffprobe
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
TUNES = {"libx264": ("film", "animation", "grain", "stillimage", "fastdecode", "zerolatency"),
         "libx265": ("animation", "grain", "fastdecode", "zerolatency")}
PROFILES = {"libx264": ("baseline", "main", "high")}
OPTIONS = {"max_height": (0, 2160, 1440, 1080, 720, 480, 360), "max_fps": (0, 60, 30, 24, 15),
           "audio": ("auto", "aac", "none"), "audio_bitrate": ("64k", "96k", "128k", "160k", "192k", "256k"), "pix_fmt": ("yuv420p",)}
RANGES = {"crf": (0, 51), "vt_quality": (1, 100), "min_savings": (0, 90), "threads": (0, 16), "nice": (0, 19)}
BOOLS = ("faststart", "copy_metadata")
SETTINGS_FILE = "encoding.json"  # em state/: ajustes feitos no painel (o config.json continua sendo o padrão)


def default_settings(cfg):
    """O processo padrão (config.json): é exatamente o que o pipeline sempre fez."""
    e = cfg["encoding"]
    prof = e["profiles"][e["default_profile"]]
    return dict(codec="libx264", crf=prof["crf"], vt_quality=65, preset=prof["preset"], tune="", profile="", pix_fmt=e.get("pixel_format", "yuv420p"),
                max_height=0, max_fps=0, audio="auto", audio_bitrate=e.get("audio_bitrate", "128k"), faststart=e.get("faststart", True),
                copy_metadata=True, min_savings=e.get("minimum_savings_percent", 15), threads=0, nice=e.get("nice", 10))


def clean_settings(raw, base):
    """Valida `raw` sobre `base`: só valores de listas/faixas conhecidas entram na linha de comando do ffmpeg (nada livre)."""
    out = dict(base)
    for k, v in (raw or {}).items():
        if k not in base:
            raise ValueError(f"parâmetro desconhecido: {k}")
        if k == "codec":
            if v not in CODECS:
                raise ValueError(f"codec não suportado: {v!r}")
        elif k == "preset":
            if v not in PRESETS:
                raise ValueError(f"preset inválido: {v!r}")
        elif k in ("tune", "profile"):
            pass  # depende do codec: validado abaixo
        elif k in OPTIONS:
            try:
                v = int(v) if isinstance(OPTIONS[k][0], int) else v
            except (TypeError, ValueError):
                raise ValueError(f"{k} inválido: {v!r}")
            if v not in OPTIONS[k]:
                raise ValueError(f"{k} inválido: {v!r}")
        elif k in RANGES:
            if isinstance(v, bool) or not str(v).lstrip("-").isdigit() or not RANGES[k][0] <= int(v) <= RANGES[k][1]:
                raise ValueError(f"{k} fora da faixa {RANGES[k]}: {v!r}")
            v = int(v)
        elif k in BOOLS:
            if not isinstance(v, bool):
                raise ValueError(f"{k} deve ser verdadeiro/falso")
        out[k] = v
    if out["tune"] and out["tune"] not in TUNES.get(out["codec"], ()):
        raise ValueError(f"tune {out['tune']!r} não existe para {out['codec']}")
    if out["profile"] and out["profile"] not in PROFILES.get(out["codec"], ()):
        raise ValueError(f"profile {out['profile']!r} não existe para {out['codec']}")
    return out


def effective_settings(cfg, state_dir):
    """Padrão do config.json + ajustes salvos pelo painel (se houver e forem válidos)."""
    base = default_settings(cfg)
    try:
        saved = json.loads((Path(state_dir) / SETTINGS_FILE).read_text()).get("settings", {})
        return clean_settings(saved, base)
    except (OSError, ValueError, AttributeError):
        return base


def settings_key(s):
    return json.dumps(s, sort_keys=True)


def target_size(info, max_height=0):
    """(largura, altura, redimensionado?) esperados na saída; dimensões sempre pares."""
    w, h = info["width"], info["height"]
    if max_height and h > max_height:
        nh = max_height // 2 * 2
        return round(w * nh / h / 2) * 2, nh, True
    return w // 2 * 2, h // 2 * 2, False


def build_ffmpeg_cmd(cfg, src, dst, info, s, ffmpeg=None):
    e = cfg["encoding"]
    codec, hw = s["codec"], s["codec"].endswith("_videotoolbox")
    has_audio = bool(info["acodec"]) and s["audio"] != "none"
    cmd = [ffmpeg or e.get("ffmpeg", "ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "0:a:0"]
    cmd += ["-c:v", codec]
    if hw:  # encoder de hardware: um único controle de qualidade (1-100, maior = melhor); não tem CRF/preset/tune
        cmd += ["-q:v", str(s["vt_quality"])]
    else:
        cmd += ["-preset", s["preset"], "-crf", str(s["crf"])]
        if s["tune"]:
            cmd += ["-tune", s["tune"]]
        if s["profile"]:
            cmd += ["-profile:v", s["profile"]]
    if CODECS[codec] == "hevc":
        cmd += ["-tag:v", "hvc1"]  # o QuickTime/Apple só reconhece HEVC em MP4 com essa tag
    cmd += ["-pix_fmt", s["pix_fmt"]]
    vf = []
    tw, th, scaled = target_size(info, s["max_height"])
    if scaled:
        vf.append(f"scale=-2:{th}")
    elif info["width"] % 2 or info["height"] % 2:  # yuv420p exige dimensões pares
        vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if s["max_fps"] and info["fps"] > s["max_fps"] + 0.5:
        vf.append(f"fps={s['max_fps']}")
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if has_audio:
        cmd += ["-c:a", "copy"] if s["audio"] == "auto" and info["acodec"] == "aac" else ["-c:a", "aac", "-b:a", s["audio_bitrate"]]
    cmd += ["-map_metadata", "0" if s["copy_metadata"] else "-1"]
    if s["faststart"]:
        cmd += ["-movflags", "+faststart"]
    if s["threads"]:
        cmd += ["-threads", str(s["threads"])]
    cmd += ["-progress", "pipe:1", "-nostats"]  # progresso legível por máquina em stdout (veja Pipeline.encode)
    return (["nice", "-n", str(s["nice"])] if s["nice"] else []) + cmd + [str(dst)]


def describe_settings(s):
    """O que o processo faz, em linguagem simples (mostrado no painel)."""
    hw = s["codec"].endswith("_videotoolbox")
    names = {"libx264": "H.264 (libx264)", "libx265": "H.265/HEVC (libx265)", "h264_videotoolbox": "H.264 por hardware (VideoToolbox)",
             "hevc_videotoolbox": "H.265/HEVC por hardware (VideoToolbox)"}
    v = f"Vídeo: recodificado em {names[s['codec']]}, "
    v += f"qualidade {s['vt_quality']}/100" if hw else f"CRF {s['crf']}, preset {s['preset']}" + (f", tune {s['tune']}" if s["tune"] else "") + (f", perfil {s['profile']}" if s["profile"] else "")
    lines = [v + f", pixels {s['pix_fmt']}.",
             "Resolução: " + (f"reduzida para no máximo {s['max_height']}p (sem ampliar)." if s["max_height"] else "mantida como no original."),
             "Quadros por segundo: " + (f"limitados a {s['max_fps']} fps." if s["max_fps"] else "mantidos como no original."),
             {"auto": f"Áudio: copiado sem perda se já for AAC; senão convertido para AAC {s['audio_bitrate']}.",
              "aac": f"Áudio: sempre recodificado em AAC {s['audio_bitrate']}.", "none": "Áudio: removido."}[s["audio"]],
             "Faixas: só o 1º vídeo e o 1º áudio são mantidos; legendas, áudios extras, anexos e dados são descartados.",
             "Metadados e capítulos: " + ("copiados." if s["copy_metadata"] else "removidos."),
             "Arquivo: MP4" + (" com faststart (começa a tocar antes de carregar tudo)." if s["faststart"] else "."),
             f"Se a economia ficar abaixo de {s['min_savings']}%, o original é copiado sem recomprimir (evita perda de qualidade à toa).",
             "Conferência: a saída só é aceita se resolução, duração (±1%), codec e presença de áudio baterem com o esperado."]
    if CODECS[s["codec"]] == "hevc":
        lines.append("Atenção: HEVC é menor, mas aparelhos antigos (como o HP Mini) podem não conseguir reproduzir.")
    if hw:
        lines.append("Atenção: o encoder de hardware é muito mais rápido, porém gera arquivos maiores para a mesma qualidade.")
    return lines


def available_encoders(ffmpeg="ffmpeg"):
    try:
        out = run([ffmpeg, "-hide_banner", "-encoders"]).stdout
    except OSError:
        return list(CODECS)
    return [c for c in CODECS if re.search(rf"^\s*V\S*\s+{c}\s", out, re.M)] or ["libx264"]


def resolve_home(cfg):
    home = Path(os.path.expanduser(cfg["pipeline"].get("home", "my-videos")))
    return home if home.is_absolute() else (ROOT / home).resolve()  # relativo à pasta do projeto


def lock_pid(state_dir):
    """PID da execução em andamento (lock em mãos de outro processo), -1 se sem PID registrado, None se parada."""
    with open(Path(state_dir) / "pipeline.lock", "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            txt = f.read().strip()
            return int(txt) if txt.isdigit() else -1
        fcntl.flock(f, fcntl.LOCK_UN)
    return None


def order_queue(rows, exts):
    """Ordem real de processamento: arquivos que não são vídeo primeiro (rápidos), vídeos por último, por chegada."""
    return sorted(rows, key=lambda j: (Path(j["src_rel"]).suffix.lower() in exts, j["id"]))


def write_json_atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def parse_progress(line, cur):
    """Atualiza `cur` com uma linha `chave=valor` do `ffmpeg -progress` (out_time_us, speed, progress)."""
    key, _, val = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):  # ambos em microssegundos (o nome *_ms do ffmpeg é histórico)
        try:
            cur["out_time"] = max(int(val), 0) / 1e6
        except ValueError:
            pass
    elif key == "speed":
        try:
            cur["speed"] = float(val.strip().rstrip("x"))
        except ValueError:
            pass
    elif key == "progress":
        cur["done"] = val.strip() == "end"
    return cur


# --------------------------------------------------------------------- remoto
class SSHRemote:
    """Toda a conversa com o HP Mini. Os testes substituem esta classe por uma falsa."""

    def __init__(self, cfg, log):
        self.c, self.log, self.host = cfg["remote"], log, None

    def _ssh_opts(self):
        o = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15",
             "-o", "ServerAliveCountMax=4", "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR", "-p", str(self.c.get("ssh_port", 22))]
        key = self.c.get("ssh_key")
        if key:
            o += ["-i", os.path.expanduser(key), "-o", "IdentitiesOnly=yes"]
        return o

    def _target(self):
        return f"{self.c['ssh_user']}@{self.host}"

    def ssh(self, command, stdin=None, timeout=120):
        try:
            r = subprocess.run(["ssh", *self._ssh_opts(), self._target(), command], input=stdin,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Transient("SSH expirou")
        if r.returncode == 255:
            raise Transient(f"SSH falhou: {r.stderr.strip()[:200]}")
        return r

    def connect(self):
        for host in [h for h in (self.c.get("local_host"), self.c.get("zerotier_host")) if h]:
            self.host = host
            try:
                if self.ssh("true", timeout=20).returncode == 0:
                    self.log.info("HP Mini acessível em %s", host)
                    return host
            except Transient as e:
                self.log.warning("%s indisponível: %s", host, e)
        self.host = None
        raise Transient("HP Mini inacessível por rede local e ZeroTier")

    def verify_storage(self, needed_bytes):
        """Recusa tudo se /srv/videos não for o microSD esperado (UUID + exfat + escrita + espaço)."""
        c = self.c
        if not c.get("expected_uuid"):
            raise Transient("expected_uuid não configurado: upload bloqueado")
        script = f"""R={shlex.quote(c['remote_root'])}
SRC=$(findmnt -n -M "$R" -o SOURCE 2>/dev/null) || {{ echo "MOUNT=no"; exit 0; }}
echo "MOUNT=yes"; echo "SOURCE=$SRC"
echo "UUID=$(lsblk -no UUID "$SRC" 2>/dev/null | head -1)"
echo "FSTYPE=$(lsblk -no FSTYPE "$SRC" 2>/dev/null | head -1)"
echo "REAL=$(readlink -f "$R")"
mkdir -p "$R/{STAGING}" 2>/dev/null && : > "$R/{STAGING}/.w" 2>/dev/null && rm -f "$R/{STAGING}/.w" && echo "WRITABLE=yes" || echo "WRITABLE=no"
echo "AVAIL=$(df -PB1 "$R" | awk 'NR==2{{print $4}}')"
"""
        out = self.ssh("sh -s", stdin=script).stdout
        info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        problems = []
        if info.get("MOUNT") != "yes":
            problems.append(f"{c['remote_root']} NÃO é ponto de montagem (microSD ausente?)")
        else:
            if info.get("UUID") != c["expected_uuid"]:
                problems.append(f"UUID {info.get('UUID')!r} != esperado {c['expected_uuid']!r}")
            if c.get("expected_filesystem") and info.get("FSTYPE") != c["expected_filesystem"]:
                problems.append(f"filesystem {info.get('FSTYPE')!r} != {c['expected_filesystem']!r}")
            if info.get("REAL") != c["remote_root"]:
                problems.append(f"caminho resolve para {info.get('REAL')!r}")
            if info.get("WRITABLE") != "yes":
                problems.append("sem permissão de escrita")
            margin = int(c.get("min_free_gb", 1) * 1024**3)
            if int(info.get("AVAIL") or 0) < needed_bytes + margin:
                problems.append(f"espaço insuficiente ({human(info.get('AVAIL'))} livres)")
        if problems:
            raise Transient("verificação do microSD falhou: " + "; ".join(problems))
        return info

    def stat(self, rel):
        """(tamanho, sha256) do arquivo remoto, ou None se não existe."""
        p = shlex.quote(f"{self.c['remote_root']}/{rel}")
        r = self.ssh(f"[ -f {p} ] && echo $(stat -c %s {p}) $(sha256sum {p} | cut -d' ' -f1)", timeout=3600)
        parts = r.stdout.split()
        return (int(parts[0]), parts[1]) if len(parts) == 2 else None

    def send(self, local, job_id):
        """Envia por `ssh cat` (o HP Mini não tem rsync; não instalamos nada lá), retomando do tamanho já gravado.
        A integridade é garantida depois por tamanho + SHA-256 (`staged`)."""
        part = shlex.quote(f"{self.c['remote_root']}/{STAGING}/{job_id}.part")
        total = os.path.getsize(local)
        have = self.ssh(f"stat -c %s {part} 2>/dev/null || echo 0").stdout.strip()
        offset = int(have) if have.isdigit() else 0
        if offset > total:  # parcial maior que o arquivo: lixo, recomeça
            offset = 0
        redirect = ">>" if offset else ">"
        if offset:
            self.log.info("Retomando upload em %s de %s", human(offset), human(total))
        with open(local, "rb") as f:
            f.seek(offset)
            try:
                r = subprocess.run(["ssh", *self._ssh_opts(), self._target(), f"cat {redirect} {part}"],
                                   stdin=f, capture_output=True, text=True)
            except OSError as e:
                raise Transient(f"envio falhou: {e}")
        if r.returncode != 0:
            raise Transient(f"envio falhou ({r.returncode}): {r.stderr.strip()[:300]}")

    def staged(self, job_id):
        return self.stat(f"{STAGING}/{job_id}.part")

    def publish(self, job_id, rel):
        """Move do staging para o nome final sem sobrescrever (mv -n) e confirma."""
        root = self.c["remote_root"]
        src, dst = shlex.quote(f"{root}/{STAGING}/{job_id}.part"), shlex.quote(f"{root}/{rel}")
        d = shlex.quote(os.path.dirname(f"{root}/{rel}"))
        r = self.ssh(f"mkdir -p {d} && [ ! -e {dst} ] && mv -n {src} {dst}")
        if r.returncode != 0:
            raise Transient(f"publicação remota falhou (destino já existe?): {r.stderr.strip()[:200]}")


# --------------------------------------------------------------------- pipeline
# upload_path / remote_rel: no modo upload são o arquivo local a enviar e o caminho remoto final;
# no modo local guardam o arquivo e o caminho relativo em ready/ (nomes herdados do modo upload).
SCHEMA = """CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY, src_rel TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 first_seen REAL, updated_at REAL, profile TEXT, decision TEXT, original_size INTEGER, output_size INTEGER,
 savings_bytes INTEGER, savings_percent REAL, duration REAL, resolution TEXT, video_codec TEXT,
 audio_codec TEXT, encoding_time REAL, upload_path TEXT, remote_rel TEXT, sha256 TEXT, error TEXT, settings TEXT,
 UNIQUE(src_rel, size, mtime_ns))"""


class Pipeline:
    def __init__(self, cfg, remote=None, dry_run=False):
        self.cfg, self.dry = cfg, dry_run
        p = cfg["pipeline"]
        self.home = resolve_home(cfg)
        self.d = {n: self.home / n for n in ("incoming", "processing", "ready", "originals", "logs", "state")}
        for d in self.d.values():
            d.mkdir(parents=True, exist_ok=True)
        self.log = self._logger()
        self.db = sqlite3.connect(self.d["state"] / "pipeline.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")  # o painel lê em paralelo sem travar as escritas
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(SCHEMA)
        if "settings" not in {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}:  # bancos criados antes dos ajustes do painel
            self.db.execute("ALTER TABLE jobs ADD COLUMN settings TEXT")
        self.remote = remote or SSHRemote(cfg, self.log)
        self.ffmpeg = cfg["encoding"].get("ffmpeg", "ffmpeg")
        self.ffprobe = cfg["encoding"].get("ffprobe", "ffprobe")
        self.exts = {e.lower() for e in p.get("extensions", DEFAULT_EXTS)}
        self._awake = None  # processo caffeinate que impede o Mac de suspender

    @property
    def local_mode(self):
        """local: comprime vídeos e copia o resto para ready/ (envio manual). upload: envia ao HP Mini por SSH."""
        return self.cfg["pipeline"].get("mode", "local") == "local"

    def is_video(self, rel):
        return Path(rel).suffix.lower() in self.exts

    def eligible(self, p):
        """Arquivo de incoming/ que o pipeline considera: não oculto (.DS_Store, ._x, pastas com ponto),
        não temporário (.part, .crdownload...) e, fora do modo local, só vídeo."""
        hidden = any(part.startswith(".") for part in p.relative_to(self.d["incoming"]).parts)
        copy_other = self.local_mode and self.cfg["pipeline"].get("copy_non_video", True)
        return p.is_file() and not hidden and not p.name.lower().endswith(TEMP_SUFFIXES) and (copy_other or self.is_video(p.name))

    def walk_incoming(self, d=None):
        """Arquivos elegíveis de incoming/ em ordem alfabética por componente (não desce em pastas ocultas)."""
        try:
            entries = sorted(os.scandir(d or self.d["incoming"]), key=lambda e: e.name)
        except FileNotFoundError:
            return
        for e in entries:
            p = Path(e.path)
            if e.is_dir(follow_symlinks=False):
                if not e.name.startswith("."):
                    yield from self.walk_incoming(e.path)
            elif self.eligible(p):
                yield p

    def _logger(self):
        log = logging.getLogger(f"videopipeline.{self.home}")
        if log.handlers:
            return log
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        for name, level in (("pipeline.log", logging.INFO), ("errors.log", logging.WARNING)):
            h = RotatingFileHandler(self.d["logs"] / name, maxBytes=2 << 20, backupCount=5)
            h.setLevel(level); h.setFormatter(fmt); log.addHandler(h)
        s = logging.StreamHandler(sys.stdout); s.setFormatter(fmt); log.addHandler(s)
        return log

    # -- banco
    def set(self, job_id, **f):
        f["updated_at"] = time.time()
        self.db.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in f)} WHERE id=?", (*f.values(), job_id))
        self.db.commit()

    def jobs(self, state):
        """Jobs no estado dado cuja próxima tentativa já venceu, na ordem de chegada."""
        return self.db.execute("SELECT * FROM jobs WHERE state=? AND next_attempt<=? ORDER BY id", (state, time.time())).fetchall()

    def fail(self, job, exc, retry_state):
        """Permanent -> FAILED (após max_attempts); Transient -> volta para `retry_state` com backoff."""
        attempts = job["attempts"] + 1
        msg = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, Permanent) and attempts >= self.cfg["pipeline"].get("max_encode_attempts", 3):
            self.log.error("FALHOU definitivamente %s: %s", job["src_rel"], msg)
            return self.set(job["id"], state="FAILED", attempts=attempts, error=msg)
        delay = min(300 * 2 ** (attempts - 1), 6 * 3600)
        self.log.warning("falha em %s (tentativa %d, nova em %ds): %s", job["src_rel"], attempts, delay, msg)
        self.set(job["id"], state=retry_state, attempts=attempts, next_attempt=time.time() + delay, error=msg)

    # -- recuperação
    def recover(self):
        (self.d["state"] / PROGRESS).unlink(missing_ok=True)
        for j in self.db.execute("SELECT * FROM jobs WHERE state IN ('PROCESSING','UPLOADING')").fetchall():
            new = "QUEUED" if j["state"] == "PROCESSING" else "UPLOAD_PENDING"
            self.log.warning("recuperando job interrompido %s: %s -> %s", j["src_rel"], j["state"], new)
            self.set(j["id"], state=new)
        for f in self.d["processing"].iterdir():  # só temporários nossos (<id>.mp4 / <id>.copy), sob o lock
            if re.fullmatch(r"\d+\.(mp4|copy)", f.name):
                f.unlink()

    # -- descoberta
    def scan(self):
        stab = self.cfg["pipeline"].get("file_stability_seconds", 180)
        inc, now, new = self.d["incoming"], time.time(), 0
        known = {(r["src_rel"], r["size"], r["mtime_ns"]): r for r in self.db.execute("SELECT * FROM jobs")}  # 1 consulta, não 1 por arquivo
        for p in self.walk_incoming():
            rel, st = p.relative_to(inc).as_posix(), p.stat()
            row = known.get((rel, st.st_size, st.st_mtime_ns))
            if row is None:
                if self.dry:
                    self.log.info("[dry-run] novo arquivo: %s (%s)", rel, human(st.st_size)); continue
                self.db.execute("UPDATE jobs SET state='SUPERSEDED' WHERE src_rel=? AND state IN ('WAITING_STABLE','QUEUED','FAILED')", (rel,))
                cur = self.db.execute("INSERT INTO jobs(src_rel,size,mtime_ns,state,first_seen,updated_at,original_size) VALUES(?,?,?,?,?,?,?)",
                                      (rel, st.st_size, st.st_mtime_ns, "WAITING_STABLE", now, now, st.st_size))
                self.db.commit()
                self.log.info("Novo arquivo: %s (%s)", rel, human(st.st_size))
                row = self.db.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
                new += 1
            if row["state"] == "WAITING_STABLE":
                if now - st.st_mtime >= stab:
                    if not self.dry:
                        self.set(row["id"], state="QUEUED")
                    self.log.info("%s estável: %s", "[dry-run]" if self.dry else "enfileirado", rel)
                else:
                    self.log.info("aguardando estabilizar: %s (modificado há %ds)", rel, now - st.st_mtime)
        return new

    # -- compressão
    def profile(self):
        """(nome, ajustes efetivos): o padrão do config.json, ou 'custom' se o painel salvou ajustes."""
        s = effective_settings(self.cfg, self.d["state"])
        return (self.cfg["encoding"]["default_profile"] if s == default_settings(self.cfg) else "custom"), s

    def build_cmd(self, src, dst, info, prof):
        return build_ffmpeg_cmd(self.cfg, src, dst, info, {**default_settings(self.cfg), **prof}, self.ffmpeg)

    def encode(self, cmd, job, info, tmp):
        """Roda o ffmpeg e publica o progresso (a cada ~1 s) em state/progress.json para o painel.
        Devolve (returncode, stderr). O stderr vai para um arquivo temporário: ler dois pipes causaria deadlock."""
        pfile = self.d["state"] / PROGRESS
        base = dict(job_id=job["id"], src_rel=job["src_rel"], size=job["size"], duration=info["duration"],
                    started_at=time.time(), out_time=0.0, speed=0.0)
        cur, last = dict(base), 0.0
        with tempfile.TemporaryFile("w+") as err:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err, text=True)
            try:
                for line in proc.stdout:
                    parse_progress(line, cur)
                    now = time.time()
                    if line.startswith("progress=") and now - last >= 1.0:
                        last = now
                        try:
                            tmp_size = tmp.stat().st_size if tmp.exists() else 0
                        except OSError:
                            tmp_size = 0
                        write_json_atomic(pfile, dict(cur, percent=min(100.0, 100.0 * cur["out_time"] / info["duration"]),
                                                      tmp_size=tmp_size, updated_at=now))
            finally:
                proc.stdout.close()
                rc = proc.wait()
                pfile.unlink(missing_ok=True)
            err.seek(0)
            return rc, err.read()

    def process(self, job):
        rel, jid = job["src_rel"], job["id"]
        src = self.d["incoming"] / rel
        st = src.stat() if src.exists() else None
        if not st or st.st_size != job["size"] or st.st_mtime_ns != job["mtime_ns"]:
            self.log.warning("arquivo mudou/sumiu antes de processar: %s", rel)
            return self.set(jid, state="SUPERSEDED")
        self.set(jid, state="PROCESSING")
        try:
            if not self.is_video(rel):
                return self.finish_local(job, src, rel, "copy", move=False, fields=dict(original_size=st.st_size, output_size=st.st_size, savings_bytes=0, savings_percent=0.0))
            info = probe(src, self.ffprobe)
            name, prof = self.profile()
            self.log.info("Input: %s | %dx%d@%s | %s/%s | %s | %.0fs", human(st.st_size), info["width"], info["height"],
                          info["fps"], info["vcodec"], info["acodec"], f"{info['bitrate']/1e6:.2f} Mbps", info["duration"])
            vids = [s for s in info["streams"] if s[0] == "video"]
            auds = [s for s in info["streams"] if s[0] == "audio"]
            dropped = [s for s in info["streams"] if s[0] not in ("video", "audio")] + vids[1:] + auds[1:]
            if dropped:
                self.log.info("Streams não copiados (legendas/dados/anexos): %s", dropped)
            need = int(st.st_size * 1.2)
            if shutil.disk_usage(self.d["processing"]).free < need:
                raise Transient(f"espaço local insuficiente (precisa ~{human(need)})")
            tmp = self.d["processing"] / f"{jid}.mp4"
            self.log.info("Profile: %s (%s, %s). Encoding started", name, prof["codec"],
                          f"q {prof['vt_quality']}" if prof["codec"].endswith("_videotoolbox") else f"crf {prof['crf']} {prof['preset']}")
            t0 = time.time()
            rc, stderr = self.encode(self.build_cmd(src, tmp, info, prof), job, info, tmp)
            enc_time = time.time() - t0
            if rc != 0:
                tmp.unlink(missing_ok=True)
                raise Permanent(f"ffmpeg falhou: {stderr.strip()[-400:]}")
            out = probe(tmp, self.ffprobe)
            exp_w, exp_h, scaled = target_size(info, prof["max_height"])
            if abs(out["width"] - exp_w) > (2 if scaled else 0) or out["height"] != exp_h or abs(out["duration"] - info["duration"]) > max(1.0, info["duration"] * 0.01) \
                    or out["vcodec"] != CODECS[prof["codec"]] or bool(out["acodec"]) != (bool(info["acodec"]) and prof["audio"] != "none"):
                tmp.unlink(missing_ok=True)
                raise Permanent(f"validação da saída falhou: {out} vs {info}")
            out_size = tmp.stat().st_size
            saved = st.st_size - out_size
            pct = 100.0 * saved / st.st_size
            self.log.info("Encoding completed em %.0fs. Output: %s. Savings: %.1f%%. Validation passed", enc_time, human(out_size), pct)
            common = dict(profile=name, original_size=st.st_size, duration=info["duration"], resolution=f"{info['width']}x{info['height']}",
                          video_codec=info["vcodec"], audio_codec=info["acodec"], encoding_time=enc_time, settings=settings_key(prof))
            if pct >= prof["min_savings"]:
                if self.local_mode:
                    return self.finish_local(job, tmp, Path(rel).with_suffix(".mp4").as_posix(), "encoded", move=True,
                                             fields=dict(output_size=out_size, savings_bytes=saved, savings_percent=pct, **common))
                dst = self.d["ready"] / Path(rel).with_suffix(".mp4")
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, dst)  # mesmo filesystem: atômico
                self.set(jid, state="UPLOAD_PENDING", decision="encoded", output_size=out_size, savings_bytes=saved,
                         savings_percent=pct, upload_path=str(dst), remote_rel=safe_rel(Path(rel).with_suffix(".mp4").as_posix()),
                         attempts=0, next_attempt=0, error=None, **common)
            else:
                tmp.unlink(missing_ok=True)
                self.log.info("Economia abaixo do mínimo: mantendo o original (sem perda geracional)")
                if self.local_mode:
                    return self.finish_local(job, src, rel, "keep_original", move=False,
                                             fields=dict(output_size=st.st_size, savings_bytes=0, savings_percent=0.0, **common))
                self.set(jid, state="UPLOAD_PENDING", decision="keep_original", output_size=st.st_size, savings_bytes=0,
                         savings_percent=0.0, upload_path=str(src), remote_rel=safe_rel(rel), attempts=0, next_attempt=0,
                         error=None, **common)
        except (Transient, Permanent) as e:
            self.fail(job, e, "QUEUED")
        except Exception as e:  # erro inesperado: não perde o job
            self.fail(job, Transient(repr(e)), "QUEUED")

    # -- modo local: tudo termina em ready/
    def place(self, path, rel_out, jid, move):
        """Põe `path` em ready/<rel_out> sem nunca sobrescrever: idêntico = reaproveita; diferente = sufixo ' (n)'.
        Cópia de arquivo que não é vídeo é feita via processing/ e verificada por SHA-256 antes de aparecer em ready/
        (o SHA da origem sai da própria leitura da cópia; só é calculado antes quando o nome já existe em ready/)."""
        size = path.stat().st_size
        base = self.d["ready"] / safe_rel(rel_out)
        base.parent.mkdir(parents=True, exist_ok=True)
        sha = sha256_file(path) if move or base.exists() else None
        dst, n = base, 1
        while dst.exists():
            if dst.stat().st_size == size and sha256_file(dst) == sha:
                self.log.info("Já existe idêntico em ready/: %s", dst.relative_to(self.d["ready"]))
                if move:
                    path.unlink(missing_ok=True)
                return dst, sha
            dst = base.with_name(f"{base.stem} ({n}){base.suffix}")
            n += 1
        if move:
            os.replace(path, dst)  # mesmo filesystem: atômico
        else:
            tmp = self.d["processing"] / f"{jid}.copy"
            sha = copy_hashed(path, tmp)  # preserva datas
            if tmp.stat().st_size != size or sha256_file(tmp) != sha:
                tmp.unlink(missing_ok=True)
                raise Transient("cópia divergente do original")
            os.replace(tmp, dst)
        return dst, sha

    def finish_local(self, job, path, rel_out, decision, move, fields):
        try:
            dst, sha = self.place(path, rel_out, job["id"], move)
        except (Transient, Permanent) as e:
            return self.fail(job, e, "QUEUED")
        out = dst.relative_to(self.d["ready"]).as_posix()
        self.set(job["id"], state="COMPLETED", decision=decision, upload_path=str(dst), remote_rel=out, sha256=sha,
                 attempts=0, next_attempt=0, error=None, **fields)
        self.log.info("Pronto em ready/%s [%s]. Job completed", out, decision)
        if self.cfg["pipeline"].get("archive_originals_after_upload", False):
            self.archive_original(job)

    # -- upload
    def upload(self, job):
        jid, rel = job["id"], job["remote_rel"]
        path = Path(job["upload_path"])
        self.set(jid, state="UPLOADING")
        try:
            if not path.exists() or path.stat().st_size != job["output_size"]:
                raise Permanent("arquivo local a enviar sumiu ou mudou de tamanho")
            size = path.stat().st_size
            sha = job["sha256"] or sha256_file(path)
            self.set(jid, sha256=sha)
            self.remote.connect()
            self.remote.verify_storage(size)
            self.log.info("HP Mini acessível; microSD verificado (UUID %s)", self.cfg["remote"]["expected_uuid"])
            final = rel
            existing = self.remote.stat(final)
            if existing:
                if existing == (size, sha):
                    self.log.info("Já existe idêntico no servidor: %s", final)
                    return self.complete(job, final)
                stem, ext = os.path.splitext(rel)
                final = f"{stem}_{sha[:8]}{ext}"
                self.log.warning("Conflito de nome (conteúdo diferente) em %s; usando %s", rel, final)
                again = self.remote.stat(final)
                if again == (size, sha):
                    return self.complete(job, final)
                if again:
                    raise Permanent(f"destino {final} existe com conteúdo diferente")
            self.log.info("Upload started: %s", final)
            self.remote.send(path, jid)
            got = self.remote.staged(jid)
            if got != (size, sha):
                raise Transient(f"verificação remota divergente: {got} != {(size, sha)}")
            self.log.info("Remote checksum verified")
            self.remote.publish(jid, final)
            if self.remote.stat(final) != (size, sha):
                raise Transient("arquivo final remoto divergente após publicação")
            self.complete(job, final)
        except (Transient, Permanent) as e:
            self.fail(job, e, "UPLOAD_PENDING")
        except Exception as e:
            self.fail(job, Transient(repr(e)), "UPLOAD_PENDING")

    def complete(self, job, final):
        self.set(job["id"], state="COMPLETED", remote_rel=final, error=None, attempts=0)
        self.log.info("Published: %s. Job completed", final)
        if job["decision"] == "encoded":
            Path(job["upload_path"]).unlink(missing_ok=True)  # derivado regenerável
        if self.cfg["pipeline"].get("archive_originals_after_upload", False):
            self.archive_original(job)

    def archive_original(self, job):
        """Move incoming/<rel> para originals/<rel> (mesma estrutura), só após envio verificado e
        só se o arquivo ainda for exatamente o processado. Nunca sobrescreve: usa sufixo se já existir."""
        src = self.d["incoming"] / job["src_rel"]
        try:
            st = src.stat()
        except FileNotFoundError:
            return
        if st.st_size != job["size"] or st.st_mtime_ns != job["mtime_ns"]:
            self.log.warning("Original mudou desde o processamento; não arquivado: %s", job["src_rel"])
            return
        dst = self.d["originals"] / job["src_rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        n = 1
        while dst.exists():
            dst = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
            n += 1
        os.replace(src, dst)  # mesmo filesystem (my-videos/ e incoming/ no mesmo volume)
        self.log.info("Original arquivado: originals/%s", dst.relative_to(self.d["originals"]))
        self.prune_chain(src.parent)  # se este era o último arquivo da pasta, ela some agora

    def prune_chain(self, d):
        """Sobe de `d` até incoming/ removendo as pastas que ficaram vazias (.DS_Store não conta como conteúdo)."""
        inc = self.d["incoming"]
        while d != inc and inc in d.parents:
            if any(c.name != ".DS_Store" for c in d.iterdir()):
                break
            (d / ".DS_Store").unlink(missing_ok=True)
            d.rmdir()
            self.log.info("Pasta vazia removida de incoming/: %s", d.relative_to(inc))
            d = d.parent

    def keep_awake(self):
        """Impede o Mac de suspender enquanto este processo roda. O caffeinate fica preso ao nosso PID (-w):
        some sozinho quando saímos, até se o processo morrer. -s só vale na tomada; -i/-m evitam suspensão por ociosidade."""
        if self._awake or not self.cfg["pipeline"].get("prevent_sleep", True) or not shutil.which("caffeinate"):
            return
        self._awake = subprocess.Popen(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())])
        self.log.info("Suspensão do Mac bloqueada enquanto houver processamento (caffeinate)")

    def release_awake(self):
        if self._awake:
            self._awake.terminate()
            self._awake = None

    def running_pid(self):
        """PID da execução em andamento (lock em mãos de outro processo) ou None."""
        return lock_pid(self.d["state"])

    def start_background(self, config_path=None):
        """Dispara `run` desacoplado do Terminal (nova sessão): continua mesmo fechando a janela."""
        pid = self.running_pid()
        if pid is not None:
            print(f"Já está rodando (PID {pid}). Acompanhe: tail -f {self.d['logs'] / 'pipeline.log'}")
            return 0
        cmd = [sys.executable, str(Path(__file__).resolve())] + (["--config", str(config_path)] if config_path else []) + ["run"]
        with open(self.d["logs"] / "background.out.log", "a") as out:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
        print(f"Rodando em segundo plano (PID {proc.pid}). Acompanhe: tail -f {self.d['logs'] / 'pipeline.log'}")
        print("Para parar: python3 main.py stop")
        return 0

    def stop_background(self):
        """Encerra a execução em andamento (incluindo o ffmpeg). Jobs interrompidos voltam à fila na próxima execução."""
        pid = self.running_pid()
        if pid is None:
            print("Nada em execução.")
            return 0
        if pid <= 0:
            print("Execução detectada, mas sem PID registrado; feche o Terminal onde ela roda.")
            return 1
        pgid = os.getpgid(pid)
        # o `start` roda em sessão própria (grupo = PID), então o grupo inclui o ffmpeg; nunca derrubamos o nosso próprio grupo
        os.killpg(pgid, signal.SIGTERM) if pgid != os.getpgrp() else os.kill(pid, signal.SIGTERM)
        print(f"Execução encerrada (PID {pid}). Os arquivos em andamento continuam em incoming/ e voltam à fila na próxima vez.")
        return 0

    def waiting_for_stable(self):
        """Quantos arquivos ainda esperam estabilizar (ignora os que já sumiram de incoming/)."""
        n = 0
        for r in self.db.execute("SELECT id, src_rel FROM jobs WHERE state='WAITING_STABLE'").fetchall():
            if (self.d["incoming"] / r["src_rel"]).exists():
                n += 1
            else:
                self.set(r["id"], state="SUPERSEDED")
        return n

    def prune_if_enabled(self):
        if self.cfg["pipeline"].get("archive_originals_after_upload", False):
            self.prune_empty_dirs()

    def prune_empty_dirs(self):
        """Varredura de segurança: remove de incoming/ pastas vazias que já estavam lá (ou que o prune_chain não pegou).
        Só mexe em pasta sem alteração há `file_stability_seconds`, para não atrapalhar uma cópia em andamento."""
        stab = self.cfg["pipeline"].get("file_stability_seconds", 180)
        inc, now = self.d["incoming"], time.time()
        mtimes = {x: x.stat().st_mtime for x in inc.rglob("*") if x.is_dir()}  # antes de remover: rmdir altera o mtime do pai
        for d in sorted(mtimes, key=lambda x: len(x.parts), reverse=True):  # das mais profundas para as mais rasas
            if d.name.startswith(".") or now - mtimes[d] < stab:
                continue
            rest = [c for c in d.iterdir() if c.name != ".DS_Store"]
            if not rest:
                (d / ".DS_Store").unlink(missing_ok=True)
                d.rmdir()
                self.log.info("Pasta vazia removida de incoming/: %s", d.relative_to(inc))

    # -- execução
    def run_once(self):
        """Uma execução completa: processa tudo o que está na fila e, se `wait_for_stable`, espera os arquivos
        recém-copiados ficarem estáveis para processá-los também. Termina quando não resta nada a fazer agora."""
        lock = open(self.d["state"] / "pipeline.lock", "a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            self.log.info("Outra execução em andamento; saindo")
            return False
        lock.seek(0); lock.truncate(); lock.write(str(os.getpid())); lock.flush()  # permite status/stop acharem o PID
        try:  # o lock é do kernel: some sozinho se o processo morrer, nunca fica "preso"
            self.recover()
            attempted = set()  # ids já tentados nesta execução: o reescaneio final só continua para jobs realmente novos
            while True:
                self.scan()
                # arquivos que não são vídeo primeiro (rápidos); vídeos por último
                queued = order_queue(self.jobs("QUEUED"), self.exts)
                if queued:
                    self.keep_awake()
                self.prune_if_enabled()  # pastas já vazias (de execuções anteriores) somem já, não só no fim da fila
                for job in queued:
                    attempted.add(job["id"])
                    self.process(job)
                    self.prune_if_enabled()  # e a cada arquivo pronto (cobre pasta que só ficou vazia agora)
                if not self.local_mode:
                    for job in self.jobs("UPLOAD_PENDING"):
                        self.upload(job)
                self.prune_if_enabled()
                if not self.cfg["pipeline"].get("wait_for_stable", True) or not self.waiting_for_stable():
                    self.scan()  # arquivos que chegaram (ex.: pelo painel) enquanto processávamos
                    if any(j["id"] not in attempted for j in self.jobs("QUEUED")):
                        continue
                    break
                self.keep_awake()
                time.sleep(self.cfg["pipeline"].get("poll_seconds", 15))
        finally:
            self.release_awake()
            lock.close()
        return True

    def retry(self):
        n = 0
        for j in self.db.execute("SELECT * FROM jobs WHERE state='FAILED'").fetchall():
            self.set(j["id"], state="UPLOAD_PENDING" if j["upload_path"] else "QUEUED", attempts=0, next_attempt=0)
            n += 1
        for j in self.db.execute("SELECT * FROM jobs WHERE state IN ('QUEUED','UPLOAD_PENDING') AND next_attempt>0").fetchall():
            self.set(j["id"], next_attempt=0)
        return n

    def status(self, check_remote=False):
        q = lambda s, *a: self.db.execute(s, a).fetchall()
        print(f"Home: {self.home}")
        pid = self.running_pid()
        print(f"Execução: {'em andamento (PID %s)' % pid if pid is not None else 'parada'}")
        print("Estados:", {r[0]: r[1] for r in q("SELECT state, COUNT(*) FROM jobs GROUP BY state")} or "(vazio)")
        for title, st in (("Fila de processamento", "QUEUED"), ("Uploads pendentes", "UPLOAD_PENDING"), ("Aguardando estabilizar", "WAITING_STABLE")):
            rows = q("SELECT * FROM jobs WHERE state=?", st)
            if rows:
                print(f"{title}:")
                for r in rows:
                    wait = f" (próxima tentativa em {int(r['next_attempt'] - time.time())}s)" if r["next_attempt"] > time.time() else ""
                    print(f"  - {r['src_rel']} {human(r['original_size'])}{wait}" + (f" ERRO: {r['error'][:100]}" if r["error"] else ""))
        print("Últimos concluídos:")
        for r in q("SELECT * FROM jobs WHERE state='COMPLETED' ORDER BY updated_at DESC LIMIT 5"):
            print(f"  - {r['remote_rel']}: {human(r['original_size'])} -> {human(r['output_size'])} ({r['savings_percent']:.1f}%) [{r['decision']}]")
        tot = q("SELECT COALESCE(SUM(savings_bytes),0), COALESCE(SUM(original_size),0) FROM jobs WHERE state='COMPLETED'")[0]
        print(f"Economia total: {human(tot[0])} de {human(tot[1])} originais")
        errs = q("SELECT src_rel, state, error FROM jobs WHERE error IS NOT NULL ORDER BY updated_at DESC LIMIT 5")
        if errs:
            print("Erros recentes:")
            for r in errs:
                print(f"  - {r['src_rel']} [{r['state']}]: {r['error'][:160]}")
        if check_remote:
            try:
                self.remote.connect()
                info = self.remote.verify_storage(0)
                print(f"Servidor OK ({self.remote.host}): {info.get('SOURCE')} UUID={info.get('UUID')} {info.get('FSTYPE')} livre={human(info.get('AVAIL'))}")
            except Transient as e:
                print(f"Servidor/microSD: PROBLEMA: {e}")
                return 1
        return 0


# --------------------------------------------------------------------- painel (só em 127.0.0.1; leitura + enviar/iniciar/parar)
DASHBOARD_HTML = ROOT / "dashboard" / "index.html"
STALE_PROGRESS = 20  # s sem atualização: o encode não está mais rodando


def peek_pid(state_dir):
    """PID da execução ativa SEM tomar o lock (o painel consulta a cada 2 s e não pode atrapalhar um `run` que esteja começando)."""
    try:
        pid = int((Path(state_dir) / "pipeline.lock").read_text().strip())
        os.kill(pid, 0)
        cmd = run(["ps", "-p", str(pid), "-o", "command="]).stdout
    except (OSError, ValueError):
        return None
    return pid if "main.py" in cmd else None


class DashboardData:
    """Monta o JSON do painel a partir do SQLite (somente leitura) e do progress.json. Guarda em cache a duração dos vídeos
    da fila (ffprobe) para estimar o tempo restante em segundos de vídeo, que escalam melhor com o encode do que bytes."""

    def __init__(self, cfg):
        self.cfg, self.home = cfg, resolve_home(cfg)
        self.exts = {e.lower() for e in cfg["pipeline"].get("extensions", DEFAULT_EXTS)}
        self.ffprobe = cfg["encoding"].get("ffprobe", "ffprobe")
        self.durations = {}  # (src_rel, size) -> segundos ou None (ilegível)

    def duration(self, rel, size, budget):
        key = (rel, size)
        if key not in self.durations:
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            try:
                self.durations[key] = probe(self.home / "incoming" / rel, self.ffprobe)["duration"]
            except (Permanent, OSError, ValueError, KeyError):
                self.durations[key] = None
        return self.durations[key]

    def progress(self):
        try:
            d = json.loads((self.home / "state" / PROGRESS).read_text())
        except (OSError, ValueError):
            return None
        return d if time.time() - d.get("updated_at", 0) <= STALE_PROGRESS else None

    def snapshot(self):
        now, pid = time.time(), peek_pid(self.home / "state")
        out = dict(now=now, running=pid is not None, pid=pid, current=None, queue=[], waiting=[], failed=[],
                   done=dict(count=0, original=0, output=0, saved=0, percent=0.0, kept=0, copied=0),
                   forecast=dict(bytes=0, saved=0, percent=0.0), eta=dict(seconds=None, approx=False), recent=[])
        dbfile = self.home / "state" / "pipeline.sqlite3"
        if not dbfile.exists():
            return out
        db = sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            q = lambda sql, *a: db.execute(sql, a).fetchall()
            t = q("""SELECT COUNT(*) n, COALESCE(SUM(original_size),0) o, COALESCE(SUM(output_size),0) f,
                     COALESCE(SUM(decision='keep_original'),0) kept, COALESCE(SUM(decision='copy'),0) copied
                     FROM jobs WHERE state='COMPLETED'""")[0]
            out["done"] = dict(count=t["n"], original=t["o"], output=t["f"], saved=t["o"] - t["f"], kept=t["kept"], copied=t["copied"],
                               percent=100.0 * (t["o"] - t["f"]) / t["o"] if t["o"] else 0.0)
            # taxas históricas dos vídeos (encodados + mantidos): economia % e segundos de encode por segundo de vídeo.
            # Só valem para o mesmo conjunto de ajustes; sem histórico com os ajustes atuais, usa tudo e marca como aproximado.
            cols = {r[1] for r in q("PRAGMA table_info(jobs)")}
            dkey, ckey = settings_key(default_settings(self.cfg)), settings_key(effective_settings(self.cfg, self.home / "state"))
            same = ("AND COALESCE(settings, ?) = ?", (dkey, ckey)) if "settings" in cols else (("", ()) if dkey == ckey else ("AND 0", ()))

            def rates(flt, args):
                v = q(f"""SELECT COUNT(*) n, COALESCE(SUM(original_size),0) o, COALESCE(SUM(output_size),0) f FROM jobs
                          WHERE state='COMPLETED' AND decision IN ('encoded','keep_original') {flt}""", *args)[0]
                e = q(f"""SELECT COUNT(*) n, COALESCE(SUM(encoding_time),0) t, COALESCE(SUM(duration),0) d, COALESCE(SUM(original_size),0) o
                          FROM jobs WHERE state='COMPLETED' AND decision='encoded' AND encoding_time>0 AND duration>0 {flt}""", *args)[0]
                return v, e
            v, e = rates(*same)
            mismatch = not (v["n"] and e["n"])
            if mismatch:
                v, e = rates("", ())
            out["settings_match"] = not mismatch
            save_frac = (v["o"] - v["f"]) / v["o"] if v["o"] else 0.0
            sec_per_media = e["t"] / e["d"] if e["d"] else None
            sec_per_byte = e["t"] / e["o"] if e["o"] else None
            out["recent"] = [dict(name=r["remote_rel"], original=r["original_size"], output=r["output_size"], percent=r["savings_percent"] or 0.0,
                                  decision=r["decision"]) for r in q("SELECT * FROM jobs WHERE state='COMPLETED' ORDER BY updated_at DESC LIMIT 8")]
            prog = self.progress() if pid is not None else None
            rows = order_queue(q("SELECT * FROM jobs WHERE state IN ('QUEUED','PROCESSING')"), self.exts)
            budget, eta, approx, vid_bytes = [8], 0.0, mismatch, 0
            for r in rows:
                is_vid = Path(r["src_rel"]).suffix.lower() in self.exts
                item = dict(rel=r["src_rel"], size=r["size"], video=is_vid, duration=None, est=None,
                            retry_in=max(0, int(r["next_attempt"] - now)), error=r["error"])
                if is_vid:
                    vid_bytes += r["size"]
                    item["duration"] = self.duration(r["src_rel"], r["size"], budget)
                    if item["duration"] and sec_per_media:
                        item["est"] = item["duration"] * sec_per_media
                    elif sec_per_byte:
                        item["est"], approx = r["size"] * sec_per_byte, True
                if r["state"] == "PROCESSING" and pid is not None:  # sem execução ativa, um PROCESSING é só um job interrompido (volta à fila)
                    cur = dict(item, percent=0.0, speed=None, out_size=None, remaining=item["est"])
                    if prog and prog.get("job_id") == r["id"]:
                        dur = prog.get("duration") or item["duration"] or 0
                        cur.update(percent=prog.get("percent", 0.0), speed=prog.get("speed") or None, out_size=prog.get("tmp_size"),
                                   duration=dur, started_at=prog.get("started_at"))
                        left = max(dur - prog.get("out_time", 0.0), 0.0)
                        cur["remaining"] = left / cur["speed"] if cur["speed"] else (left * sec_per_media if sec_per_media else None)
                    out["current"] = cur
                    eta += cur["remaining"] or 0
                else:
                    out["queue"].append(item)
                    eta += item["est"] or 0
            out["queue"] = out["queue"][:500]
            if sec_per_media or sec_per_byte or (out["current"] and out["current"]["remaining"]):
                out["eta"] = dict(seconds=eta, approx=approx)
            out["forecast"] = dict(bytes=vid_bytes, saved=int(vid_bytes * save_frac), percent=100.0 * save_frac)
            out["waiting"] = [dict(rel=r["src_rel"], size=r["size"]) for r in q("SELECT * FROM jobs WHERE state='WAITING_STABLE'")]
            out["failed"] = [dict(rel=r["src_rel"], error=r["error"]) for r in q("SELECT * FROM jobs WHERE state='FAILED'")]
        finally:
            db.close()
        return out


def incoming_target(incoming, rel):
    """Caminho dentro de incoming/ para um envio do painel. Recusa caminho absoluto, `..`, nome oculto ou vazio."""
    rel = unquote(rel or "").replace("\\", "/")
    parts = rel.split("/")
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") or "\x00" in p or p.startswith(".") for p in parts):
        raise ValueError(f"caminho recusado: {rel!r}")
    target = Path(incoming).joinpath(*parts)
    if Path(incoming).resolve() not in target.resolve().parents:
        raise ValueError(f"caminho fora de incoming/: {rel!r}")
    return target


def save_upload(incoming, rel, stream, length, mtime_ms=None):
    """Grava `length` bytes de `stream` em incoming/<rel> sem nunca sobrescrever (sufixo ' (n)' se já existir).
    Escreve em <nome>.part (ignorado pelo pipeline) e só então renomeia; preserva a data de modificação original."""
    dst = incoming_target(incoming, rel)
    if shutil.disk_usage(incoming).free < length + (1 << 30):
        raise OSError("espaço em disco insuficiente")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    try:
        with open(tmp, "wb") as f:
            left = length
            while left > 0:
                chunk = stream.read(min(4 << 20, left))
                if not chunk:
                    raise OSError("envio interrompido")
                f.write(chunk)
                left -= len(chunk)
        if mtime_ms:
            t = min(mtime_ms / 1000.0, time.time())
            os.utime(tmp, (t, t))
        n = 1
        while dst.exists():
            dst = dst.with_name(f"{Path(rel).stem} ({n}){Path(rel).suffix}")
            n += 1
        os.replace(tmp, dst)  # não existe mais nada com esse nome: nunca sobrescreve
    finally:
        tmp.unlink(missing_ok=True)
    return dst.relative_to(incoming).as_posix()


def spawn_run(home, config_path=None):
    """Inicia `main.py run` desacoplado (nova sessão). Devolve (pid, iniciou_agora)."""
    pid = peek_pid(home / "state")
    if pid:
        return pid, False
    cmd = [sys.executable, str(Path(__file__).resolve())] + (["--config", str(config_path)] if config_path else []) + ["run"]
    with open(home / "logs" / "background.out.log", "a") as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return proc.pid, True


def stop_run(home):
    """Encerra a execução ativa (e o ffmpeg dela). Só derruba o grupo se o processo for líder dele (nunca o grupo de um terminal)."""
    pid = peek_pid(home / "state")
    if not pid:
        return None
    if os.getpgid(pid) == pid:
        os.killpg(pid, signal.SIGTERM)
    else:
        os.kill(pid, signal.SIGTERM)
    return pid


def settings_payload(cfg, state_dir, encoders):
    """Tudo o que o painel precisa para a tela de ajustes: valores atuais/padrão, textos e o comando equivalente."""
    default, cur = default_settings(cfg), effective_settings(cfg, state_dir)
    sample = dict(width=1920, height=1080, fps=30.0, acodec="aac", vcodec="h264")  # exemplo ilustrativo: 1080p30 com áudio AAC
    cmd = build_ffmpeg_cmd(cfg, "entrada.mp4", "saida.mp4", sample, cur)
    if cmd[0] == "nice":
        cmd = cmd[3:]
    i = cmd.index("-progress")
    del cmd[i:i + 3]  # flags internas do painel: não fazem parte do "processo"
    e = cfg["encoding"]
    return dict(settings=cur, defaults=default, custom=cur != default, description=describe_settings(cur), default_description=describe_settings(default),
                preview=shlex.join(cmd), encoders=encoders, profiles=e["profiles"], default_profile=e["default_profile"],
                options=dict(presets=PRESETS, tunes=TUNES, profiles=PROFILES, ranges=RANGES, **{k: v for k, v in OPTIONS.items() if k != "pix_fmt"}))


def save_settings(cfg, state_dir, raw, reset=False):
    """Valida e grava os ajustes do painel em state/encoding.json (igual ao padrão = apaga o arquivo)."""
    path = Path(state_dir) / SETTINGS_FILE
    base = default_settings(cfg)
    new = base if reset else clean_settings(raw, base)
    if new == base:
        path.unlink(missing_ok=True)
    else:
        write_json_atomic(path, {"settings": new})
    return new


def serve_dashboard(cfg, port, config_path=None, open_browser=False):
    data = DashboardData(cfg)
    token = secrets.token_urlsafe(24)  # só quem carregou a página consegue escrever (outros sites não leem a página: sem CORS)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}  # recusa outros Host (DNS rebinding)
    incoming, state = data.home / "incoming", data.home / "state"
    encoders = available_encoders(cfg["encoding"].get("ffmpeg", "ffmpeg"))
    for n in ("incoming", "processing", "ready", "originals", "logs", "state"):  # mesma estrutura que o Pipeline cria
        (data.home / n).mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code, body, ctype="application/json"):
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _ok(self):
            if self.headers.get("Host") not in hosts:
                self.close_connection = True
                self._send(403, {"error": "host não permitido"})
                return False
            return True

        def do_GET(self):
            if not self._ok():
                return
            path = self.path.split("?")[0]
            try:
                if path == "/api/status":
                    snap = data.snapshot()
                    snap["free"] = shutil.disk_usage(data.home).free
                    self._send(200, snap)
                elif path == "/api/settings":
                    self._send(200, settings_payload(cfg, state, encoders))
                elif path in ("/", "/index.html"):
                    html = DASHBOARD_HTML.read_text().replace("__TOKEN__", token)
                    self._send(200, html.encode(), "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception as e:  # o painel nunca derruba o servidor
                self._send(500, {"error": repr(e)})

        def do_POST(self):
            if not self._ok():
                return
            if not secrets.compare_digest(self.headers.get("X-VP-Token", ""), token):
                self.close_connection = True
                return self._send(403, {"error": "token inválido: recarregue a página"})
            path = self.path.split("?")[0]
            try:
                if path == "/api/upload":
                    if peek_pid(data.home / "state"):  # só se aceitam arquivos com o processamento parado
                        self.close_connection = True
                        return self._send(409, {"error": "processamento em andamento: pare ou aguarde terminar"})
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0:
                        return self._send(411, {"error": "Content-Length obrigatório"})
                    try:
                        mtime = int(self.headers.get("X-Mtime", "0")) or None
                        saved = save_upload(incoming, self.headers.get("X-Rel", ""), self.rfile, length, mtime)
                    except ValueError as e:
                        self.close_connection = True
                        return self._send(400, {"error": str(e)})
                    except OSError as e:
                        self.close_connection = True
                        return self._send(507, {"error": str(e)})
                    self._send(200, {"saved": saved})
                elif path == "/api/settings":
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 16384:
                        self.close_connection = True
                        return self._send(413, {"error": "corpo grande demais"})
                    raw_body = self.rfile.read(length) or b"{}"
                    if peek_pid(state):  # mudar no meio da fila misturaria dois processos: só com tudo parado
                        return self._send(409, {"error": "processamento em andamento: pare ou aguarde terminar"})
                    try:
                        body = json.loads(raw_body)
                        raw = body.get("settings", {})
                        if not isinstance(raw, dict) or (raw.get("codec", "libx264") not in encoders):
                            raise ValueError("codec indisponível neste ffmpeg")
                        save_settings(cfg, state, raw, reset=bool(body.get("reset")))
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                    self._send(200, settings_payload(cfg, state, encoders))
                elif path == "/api/start":
                    pid, started = spawn_run(data.home, config_path)
                    self._send(200, {"pid": pid, "started": started})
                elif path == "/api/stop":
                    self._send(200, {"stopped": stop_run(data.home)})
                else:
                    self._send(404, {"error": "not found"})
            except Exception as e:
                self.close_connection = True
                self._send(500, {"error": repr(e)})

        def log_message(self, *a):
            pass

    url = f"http://127.0.0.1:{port}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # só loopback: não aparece na rede
    except OSError:  # porta ocupada: provavelmente o painel já está aberto em outro terminal
        print(f"A porta {port} já está em uso; se for o painel, ele continua em {url}")
        if open_browser:
            webbrowser.open(url)
        return 1
    print(f"Painel em {url}  (Ctrl+C para fechar)")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="VideoPipeline")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n, h in (("run", "processa tudo agora, em primeiro plano (bloqueia a suspensão do Mac)"),
                 ("start", "igual ao run, mas em segundo plano (continua se fechar o Terminal)"), ("stop", "encerra a execução em andamento"), ("scan", "só descobrir arquivos novos"),
                 ("retry", "recolocar falhas na fila"), ("dry-run", "mostrar o que seria feito, sem alterar nada")):
        sub.add_parser(n, help=h)
    s = sub.add_parser("status", help="estado geral"); s.add_argument("--remote", action="store_true", help="verificar servidor e microSD")
    for name in ("app", "dashboard"):  # `app` abre o navegador; `dashboard` só sobe o servidor
        d = sub.add_parser(name, help="painel em http://127.0.0.1:<porta>: enviar arquivos, iniciar, parar e acompanhar"
                           + (" (abre o navegador)" if name == "app" else ""))
        d.add_argument("--port", type=int, default=8765)
        d.add_argument("--no-open", action="store_true", help="não abrir o navegador")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.cmd in ("app", "dashboard"):  # sem criar Pipeline: não toca em lock nem em banco
        return serve_dashboard(cfg, args.port, args.config, open_browser=args.cmd == "app" and not args.no_open)
    if args.cmd == "dry-run":
        cfg["pipeline"]["dry_run"] = True
    p = Pipeline(cfg, dry_run=cfg["pipeline"].get("dry_run", False))
    if args.cmd in ("run", "dry-run") and p.dry:
        p.scan()
        for f in sorted(p.d["incoming"].rglob("*")):
            if p.eligible(f) and p.is_video(f.name):
                try:
                    i = probe(f, p.ffprobe)
                    p.log.info("[dry-run] %s: %dx%d %s/%s %.0fs", f.name, i["width"], i["height"], i["vcodec"], i["acodec"], i["duration"])
                except Permanent as e:
                    p.log.warning("[dry-run] %s ilegível: %s", f.name, e)
        return 0
    if args.cmd == "run":
        p.run_once()
    elif args.cmd == "start":
        return p.start_background(args.config)
    elif args.cmd == "stop":
        return p.stop_background()
    elif args.cmd == "scan":
        p.scan()
    elif args.cmd == "retry":
        print(f"{p.retry()} job(s) recolocado(s) na fila")
    elif args.cmd == "status":
        return p.status(args.remote)
    return 0


if __name__ == "__main__":
    sys.exit(main())
