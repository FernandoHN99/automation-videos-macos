#!/bin/bash
# Diagnóstico rápido: ferramentas, agente launchd, estado do pipeline e servidor/microSD.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "== ferramentas =="; for t in python3 ffmpeg ffprobe ssh; do printf "%-8s %s\n" "$t" "$(command -v $t || echo AUSENTE)"; done
echo "== launchd =="; launchctl print "gui/$(id -u)/com.hpmini.videopipeline" 2>&1 | grep -E "state =|runs =|last exit|Could not" || echo "agente não carregado"
echo "== chave SSH (só usada no modo upload) =="; ls -l ~/.ssh/videopipeline_ed25519 2>&1 | awk '{print $1, $NF}'
echo "== pipeline =="; python3 "$ROOT/main.py" status
echo "== servidor/microSD (só relevante no modo upload) =="; python3 "$ROOT/main.py" status --remote | tail -1
echo "== últimos erros =="; tail -n 5 "$ROOT/my-videos/logs/errors.log" 2>/dev/null || echo "(nenhum)"
