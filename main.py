#!/usr/bin/env python3
"""VideoPipeline: comprime vídeos no Mac (FFmpeg) e entrega tudo pronto em my-videos/ready/.

Modo "local" (padrão): incoming/ -> (estável?) -> vídeo: ffprobe -> ffmpeg -> validação; outros arquivos: cópia
verificada -> ready/ (mesma estrutura de pastas) -> original arquivado em originals/. O envio ao microSD é manual.
Modo "upload" (opcional): após a validação, envia ao HP Mini por SSH (verifica UUID/exFAT, SHA-256, publica atômico).

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
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMP_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".aria2", ".!qb")
BAD_CHARS = re.compile(r'[\x00-\x1f"*:<>?\\|]')
STAGING = ".pipeline-tmp"  # dentro de remote_root, mesmo filesystem do destino final


class Transient(Exception):
    """Falha temporária (rede, SSH, microSD ausente): tenta de novo mais tarde."""


class Permanent(Exception):
    """Falha que não se resolve sozinha (arquivo corrompido, ffmpeg falhou)."""


# --------------------------------------------------------------------- utilidades
def load_config(path=None):
    path = Path(path or os.environ.get("VIDEOPIPELINE_CONFIG") or ROOT / "support" / "config" / "config.json")
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
 audio_codec TEXT, encoding_time REAL, upload_path TEXT, remote_rel TEXT, sha256 TEXT, error TEXT,
 UNIQUE(src_rel, size, mtime_ns))"""


class Pipeline:
    def __init__(self, cfg, remote=None, dry_run=False):
        self.cfg, self.dry = cfg, dry_run
        p = cfg["pipeline"]
        home = Path(os.path.expanduser(p.get("home", "my-videos")))
        self.home = home if home.is_absolute() else (ROOT / home).resolve()  # relativo à pasta do projeto
        self.d = {n: self.home / n for n in ("incoming", "processing", "ready", "originals", "logs", "state")}
        for d in self.d.values():
            d.mkdir(parents=True, exist_ok=True)
        self.log = self._logger()
        self.db = sqlite3.connect(self.d["state"] / "pipeline.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute(SCHEMA)
        self.remote = remote or SSHRemote(cfg, self.log)
        self.ffmpeg = cfg["encoding"].get("ffmpeg", "ffmpeg")
        self.ffprobe = cfg["encoding"].get("ffprobe", "ffprobe")
        self.exts = {e.lower() for e in p.get("extensions", [".mp4", ".mov", ".mkv", ".m4v", ".webm"])}

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
        for p in sorted(inc.rglob("*")):
            if not self.eligible(p):
                continue
            rel, st = p.relative_to(inc).as_posix(), p.stat()
            row = self.db.execute("SELECT * FROM jobs WHERE src_rel=? AND size=? AND mtime_ns=?",
                                  (rel, st.st_size, st.st_mtime_ns)).fetchone()
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
        e = self.cfg["encoding"]
        name = e["default_profile"]
        return name, e["profiles"][name]

    def build_cmd(self, src, dst, info, prof):
        e = self.cfg["encoding"]
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:v:0"]
        if info["acodec"]:
            cmd += ["-map", "0:a:0"]
        cmd += ["-c:v", "libx264", "-preset", prof["preset"], "-crf", str(prof["crf"]), "-pix_fmt", e.get("pixel_format", "yuv420p")]
        if info["width"] % 2 or info["height"] % 2:  # libx264 yuv420p exige dimensões pares
            cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        if info["acodec"] == "aac":
            cmd += ["-c:a", "copy"]
        elif info["acodec"]:
            cmd += ["-c:a", "aac", "-b:a", e.get("audio_bitrate", "128k")]
        cmd += ["-map_metadata", "0"]
        if e.get("faststart", True):
            cmd += ["-movflags", "+faststart"]
        nice = e.get("nice", 10)
        return (["nice", "-n", str(nice)] if nice else []) + cmd + [str(dst)]

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
            self.log.info("Profile: %s (crf %s, %s). Encoding started", name, prof["crf"], prof["preset"])
            t0 = time.time()
            r = run(self.build_cmd(src, tmp, info, prof))
            enc_time = time.time() - t0
            if r.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise Permanent(f"ffmpeg falhou: {r.stderr.strip()[-400:]}")
            out = probe(tmp, self.ffprobe)
            exp_w, exp_h = info["width"] // 2 * 2, info["height"] // 2 * 2
            if (out["width"], out["height"]) != (exp_w, exp_h) or abs(out["duration"] - info["duration"]) > max(1.0, info["duration"] * 0.01) \
                    or out["vcodec"] != "h264" or bool(out["acodec"]) != bool(info["acodec"]):
                tmp.unlink(missing_ok=True)
                raise Permanent(f"validação da saída falhou: {out} vs {info}")
            out_size = tmp.stat().st_size
            saved = st.st_size - out_size
            pct = 100.0 * saved / st.st_size
            self.log.info("Encoding completed em %.0fs. Output: %s. Savings: %.1f%%. Validation passed", enc_time, human(out_size), pct)
            common = dict(profile=name, original_size=st.st_size, duration=info["duration"], resolution=f"{info['width']}x{info['height']}",
                          video_codec=info["vcodec"], audio_codec=info["acodec"], encoding_time=enc_time)
            if pct >= self.cfg["encoding"].get("minimum_savings_percent", 15):
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
        Cópia de arquivo que não é vídeo é feita via processing/ e verificada por SHA-256 antes de aparecer em ready/."""
        size, sha = path.stat().st_size, sha256_file(path)
        base = self.d["ready"] / safe_rel(rel_out)
        base.parent.mkdir(parents=True, exist_ok=True)
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
            shutil.copy2(path, tmp)  # preserva datas
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

    def prune_empty_dirs(self):
        """Remove de incoming/ as pastas que ficaram vazias (só com .DS_Store) depois de arquivar os originais.
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
        lock = open(self.d["state"] / "pipeline.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            self.log.info("Outra execução em andamento; saindo")
            return False
        try:  # o lock é do kernel: some sozinho se o processo morrer, nunca fica "preso"
            self.recover()
            self.scan()
            # arquivos que não são vídeo primeiro (rápidos); vídeos por último, do menor para o maior tempo de espera
            for job in sorted(self.jobs("QUEUED"), key=lambda j: (self.is_video(j["src_rel"]), j["id"])):
                self.process(job)
            if not self.local_mode:
                for job in self.jobs("UPLOAD_PENDING"):
                    self.upload(job)
            if self.cfg["pipeline"].get("archive_originals_after_upload", False):
                self.prune_empty_dirs()
        finally:
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="VideoPipeline")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n, h in (("run", "ciclo completo: escanear, comprimir/copiar para ready/ (ou enviar, no modo upload)"), ("scan", "só descobrir arquivos novos"),
                 ("retry", "recolocar falhas na fila"), ("dry-run", "mostrar o que seria feito, sem alterar nada")):
        sub.add_parser(n, help=h)
    s = sub.add_parser("status", help="estado geral"); s.add_argument("--remote", action="store_true", help="verificar servidor e microSD")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
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
    elif args.cmd == "scan":
        p.scan()
    elif args.cmd == "retry":
        print(f"{p.retry()} job(s) recolocado(s) na fila")
    elif args.cmd == "status":
        return p.status(args.remote)
    return 0


if __name__ == "__main__":
    sys.exit(main())
