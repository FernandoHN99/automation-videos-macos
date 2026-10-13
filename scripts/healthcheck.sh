#!/bin/bash
# Diagnóstico rápido: ferramentas, agente launchd, estado do pipeline e servidor/microSD.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "== ferramentas =="; for t in python3 ffmpeg ffprobe ssh; do printf "%-8s %s\n" "$t" "$(command -v $t || echo AUSENTE)"; done
echo "== launchd =="; launchctl print "gui/$(id -u)/com.hpmini.videopipeline" 2>&1 | grep -E "state =|runs =|last exit|Could not" || echo "agente não carregado"
echo "== chave SSH =="; ls -l ~/.ssh/videopipeline_ed25519 2>&1 | awk '{print $1, $NF}'
echo "== pipeline =="; python3 "$ROOT/scripts/video_pipeline.py" --config "$ROOT/config/config.json" status --remote
echo "== últimos erros =="; tail -n 5 "$ROOT/my-videos/logs/errors.log" 2>/dev/null || echo "(nenhum)"
