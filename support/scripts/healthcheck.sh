#!/bin/bash
# Diagnóstico rápido: ferramentas, estado do pipeline e últimos erros.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "== ferramentas =="; for t in python3 ffmpeg ffprobe caffeinate; do printf "%-11s %s\n" "$t" "$(command -v $t || echo AUSENTE)"; done
echo "== pipeline =="; python3 "$ROOT/main.py" status
echo "== últimos erros =="; tail -n 5 "$ROOT/my-videos/logs/errors.log" 2>/dev/null || echo "(nenhum)"
echo "== servidor/microSD (só relevante no modo upload) =="; python3 "$ROOT/main.py" status --remote | tail -1
