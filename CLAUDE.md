# VideoPipeline — contexto do projeto

Automação local no Mac. O usuário coloca vídeos (e pastas inteiras de aula) em `my-videos/incoming/`; o script comprime os vídeos com FFmpeg, copia os demais arquivos iguais e deixa tudo em `my-videos/ready/`, com a mesma estrutura de pastas. Toda conversão acontece no Mac.

**Escopo (2026-10-14): só o vídeo.** O projeto comprime, copia os materiais e entrega em `ready/`. O que acontece depois (levar a pasta para outro dispositivo ou servidor) está fora do projeto. O antigo modo de envio por SSH ao HP Mini (e tudo ligado a ele: UUID/exFAT do microSD, `status --remote`, estados `UPLOAD_*`, bloco `remote` do config) foi removido; está no histórico do git (commit `2836a49`).

Idioma de trabalho: português (Brasil). Leia o [README.md](README.md) (resumo e comandos) e o [guia completo](docs/GUIA.md) (configuração, perfis, painel).

## Regras do usuário (valem sempre)

- Não apagar, mover nem sobrescrever originais sem autorização. (Autorizado: após ficar pronto, o original é **movido** de `incoming/` para `originals/`; nunca apagado.)
- Sem Docker, VMs, IA residente ou dependências pesadas: só Python 3 (biblioteca padrão), FFmpeg e `caffeinate`.
- Nunca sobrescrever arquivo de destino.
- Sem senhas ou chaves em scripts, JSON ou logs.
- Pedir aprovação antes de operações irreversíveis (ex.: apagar arquivos).

## Estado atual (2026-10-14)

- Vídeos → comprimidos (libx264, perfil `compact` = CRF 23, resolução e FPS originais; se economizar < 15% copia o original). Outros arquivos (PDF, imagem, zip...) → copiados iguais, SHA-256 conferido, datas preservadas. Tudo termina em `ready/`.
- **Execução manual, sem agendamento (decisão do usuário, 2026-10-13):** o LaunchAgent foi removido. `python3 main.py app` abre o painel (enviar, iniciar, parar, acompanhar); `python3 main.py start` roda em segundo plano (nova sessão, sobrevive ao Terminal); `run` em primeiro plano; `stop` encerra (mata o grupo de processos, inclusive o ffmpeg); `status` mostra o PID. Enquanto há trabalho, o `caffeinate -i -m -s -w <pid>` bloqueia a suspensão (verificado com `pmset -g assertions`); fechar a tampa do MacBook sem monitor externo suspende mesmo assim. O `run` espera arquivos recém-copiados ficarem estáveis (`wait_for_stable`, `poll_seconds`) e só termina quando não resta nada. O PID fica em `my-videos/state/pipeline.lock`.
- Perfil `compact` e `archive_originals = true` aprovados pelo usuário. Fluxo: validado/copiado → original vai de `incoming/` para `originals/`; `ready/` fica com os vídeos finais e os arquivos copiados; a pasta que fica vazia em `incoming/` é removida assim que o último arquivo dela é arquivado (`prune_chain`, sobe até a raiz); uma varredura de segurança (`prune_empty_dirs`) cuida de pastas vazias antigas, só as sem alteração há `file_stability_seconds`.
- **Teste real concluído (2026-10-13):** `incoming/aula-01` (vídeo VP9 1080p de 2h11, 1,2 GB → 828 MB H.264, −30,7%, 27 min; 236 materiais idênticos por SHA-256 e datas preservadas). `incoming/` ficou limpo.
- Pendente (do usuário): conferir a qualidade da aula de 2h antes de apagar `originals/`.

## Estrutura

```
main.py                      pipeline + CLI + servidor do painel (arquivo único)
config/config.json           configuração
dashboard/                   interface web de tela única (python3 main.py app → http://127.0.0.1:8765); módulos ES sem build, servidos em /static
  index.html                   casca: meta do token, <div id="app">, css e js
  css/                         tokens.css (cores, tipo, espaço; tema escuro e claro), base.css, console.css (grade) e components/*.css (um por componente)
  js/app.js                    raiz de composição: store, controladores e montagem dos componentes
  js/core/                     dom.js (h(), sem innerHTML), format.js, store.js (createStore + watch), api.js, tween.js, icons.js
  js/features/                 regras sem DOM: staging, uploader, drop-reader, file-drops/picker, timeline-model, settings-model e os controladores (run, staging, settings)
  js/components/               um arquivo por componente: Topbar, Hero (HeroSlate/HeroStaged/HeroLive), Timeline, SavingsPanel, DiskPanel, QueuePanel, RecentPanel, RecipePanel, SettingsDrawer, DropOverlay, Toasts, ConfirmDialog
scripts/healthcheck.sh       diagnóstico
tests/                       65 testes: python3 -m unittest discover -s tests
docs/GUIA.md, docs/painel.jpg  guia completo e imagem do README (README.md é curto: resumo + comandos)
my-videos/                   dados, ignorados pelo git (.gitkeep mantém a estrutura)
  incoming/ processing/ ready/ originals/ logs/ state/
```

## Painel (python3 main.py app | dashboard)

- Interface (reescrita em 2026-10-14): console de tela única; o herói é uma máquina de estados (`heroMode`: empty/waiting/staged/live) que mostra a carta de barras SMPTE como área de soltar e, processando, a leitura ao vivo. Convenções do front: componente = função `Nome(context)` que devolve `{ el, update(state) }`; `update` é um `watch(selecionar, desenhar)` que só redesenha quando a fatia do estado muda (o envio dispara dezenas de atualizações por segundo); todo DOM sai de `h()` (nunca innerHTML com dados); regras sem DOM ficam em `js/features` e os componentes só desenham; cor, tipo e espaço só via tokens. Sem build: `tests/` confere que todo import, @import e `/static/` aponta para arquivo existente.
- Servidor `ThreadingHTTPServer` só em `127.0.0.1`, **sem criar `Pipeline`** (não toma o lock). Leitura: `DashboardData` abre o SQLite em `mode=ro` e lê `state/progress.json`; `peek_pid` descobre a execução sem pegar o `flock`.
- `/static/<arquivo>` serve só `.css`, `.js` e `.svg` de `dashboard/` (`static_asset`: recusa `..`, caminho absoluto e outros tipos); o `Host` é checado como nas demais rotas. `/api/status` inclui `free` e `disk_total`.
- Escrita (POST) exige o token por abertura da página (`X-VP-Token`, embutido no HTML) e `Host` em 127.0.0.1/localhost:porta. `/api/upload` só aceita com o processamento parado (409 se `peek_pid`) e grava em `incoming/<rel>.part` e renomeia (nunca sobrescreve: sufixo ` (n)`; preserva mtime; recusa `..`, absolutos e ocultos via `incoming_target`); `/api/start` = `spawn_run`; `/api/stop` = `stop_run` (killpg só se o processo for líder do grupo).
- Ajustes de compressão: o padrão é `default_settings(cfg)` (config.json = o processo de sempre; `build_ffmpeg_cmd` com o padrão gera o mesmo comando de antes, coberto por teste). O painel salva ajustes validados (`clean_settings`: só listas/faixas conhecidas) em `state/encoding.json` (`save_settings`/`effective_settings`); `Pipeline.profile()` devolve ('custom', ajustes) quando diferem. Só editáveis com o processamento parado (409). Cada job guarda `settings` (JSON); ETA e previsão usam o histórico com os mesmos ajustes (legado NULL = padrão).
- Pastas vazias de `incoming/`: `prune_if_enabled()` roda antes da fila e depois de cada arquivo (não só no fim); `prune_chain` continua removendo na hora a pasta cujo último arquivo foi arquivado.
- Snapshot do painel inclui `session` (concluídos desde o início da execução atual, via mtime do lock) para a linha do tempo; a largura dos segmentos é proporcional ao tempo de encode (feitos: `encoding_time`; fila: `est`).
- Progresso ao vivo: `Pipeline.encode` roda o ffmpeg com `-progress pipe:1` e grava `state/progress.json` (~1 s; apagado ao fim e no `recover`).
- `run_once` reescaneia antes de sair, para pegar arquivos enviados durante o processamento (só continua para jobs novos).
- SQLite em WAL; `place()` calcula o SHA da origem durante a cópia (`copy_hashed`); `scan()` usa `walk_incoming` + 1 consulta.
- Estado e histórico: `my-videos/state/pipeline.sqlite3` (não apagar: evita reprocessar).
- Ordem da fila: arquivos que não são vídeo primeiro, vídeos por último (cada original vai para `originals/` assim que o seu arquivo fica pronto).
- Estados dos jobs: WAITING_STABLE, QUEUED, PROCESSING, COMPLETED, FAILED, SUPERSEDED. Retentativas por `next_attempt` com backoff 5 min → 6 h.
- Colunas `output_path`/`output_rel` do SQLite guardam o arquivo final em `ready/` (antes `upload_path`/`remote_rel`): `open_db` renomeia bancos antigos ao abrir, tanto no `Pipeline` quanto ao subir o painel com a execução parada. Estados antigos `UPLOADING`/`UPLOAD_PENDING` voltam para `QUEUED` no `recover`.
- Nomes de saída são ajustados para qualquer sistema de arquivos (`" * : < > ? \ |` viram `_`, Unicode NFC, até 200 bytes).
- Lock com `fcntl.flock` em `my-videos/state/pipeline.lock` (macOS não tem `flock`).

## Ambiente (verificado)

- Mac: macOS 26.6.2, Apple M1, 16 GB. Python 3.14, FFmpeg 9.0.2 (libx264, libvmaf, VideoToolbox).

## Medições de qualidade

Screencast de programação (1080p, trecho de 2 min): CRF 18 → −30%, CRF 20 → −40%, CRF 23 → −54% (VMAF 96,9 / 96,6 / 96,0); texto legível no CRF 23. Vídeo de pessoa falando (5,2 Mbps): CRF 18/20 ficaram **maiores** que o original; por isso a regra dos 15%. Detalhes em docs/GUIA.md.

## Git

Remote `origin`: https://github.com/FernandoHN99/automation-videos-macos (**PRIVADO**), branch `main`. `config/config.json` é versionado e hoje não tem dados de rede, mas o **histórico** antigo ainda contém IPs da LAN/ZeroTier, usuário e UUID do microSD (nenhum segredo): **não tornar o repositório público sem limpar o histórico**.
