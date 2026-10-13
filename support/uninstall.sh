#!/bin/bash
# Remove só o LaunchAgent. NÃO apaga vídeos, originais, logs nem o banco de histórico.
LABEL=com.hpmini.videopipeline
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "agente descarregado" || echo "agente não estava carregado"
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" && echo "plist removido"
echo "Dados preservados na pasta do projeto"
