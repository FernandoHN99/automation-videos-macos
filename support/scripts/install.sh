#!/bin/bash
# Instala o VideoPipeline para o usuário atual (sem root).
#   install.sh           cria pastas e gera o plist, mas NÃO carrega o agente
#   install.sh --load    também carrega o LaunchAgent (ativa a automação)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"  # raiz do projeto (onde está o main.py)
PY="$(command -v python3)"  # caminho estável (não o da versão em Cellar, que muda no brew upgrade)
HOME_DIR="$ROOT/my-videos"  # pastas de trabalho (incoming, ready, logs...)
LABEL=com.hpmini.videopipeline
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

for t in ffmpeg ffprobe ssh; do command -v "$t" >/dev/null || { echo "faltando: $t"; exit 1; }; done
mkdir -p "$HOME_DIR"/{incoming,processing,ready,originals,failed,logs,state} "$HOME/Library/LaunchAgents"

sed -e "s#__PYTHON__#$PY#" -e "s#__SCRIPT__#$ROOT/main.py#" \
    -e "s#__CONFIG__#$ROOT/support/config/config.json#" -e "s#__HOME__#$HOME_DIR#g" \
    "$ROOT/support/launchd/$LABEL.plist" > "$PLIST"
plutil -lint "$PLIST"
echo "plist: $PLIST"

if [ "${1:-}" = "--load" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl print "gui/$(id -u)/$LABEL" | grep -E "state|runs|last exit" || true
  echo "Agente carregado: roda ao carregar e a cada 15 min."
else
  echo "Agente NÃO carregado. Para ativar: $0 --load"
fi
