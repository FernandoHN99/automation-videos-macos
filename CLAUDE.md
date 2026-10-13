# VideoPipeline — contexto do projeto

Automação local no Mac: o usuário coloca vídeos em `~/VideoPipeline/incoming/`; o Mac comprime com FFmpeg (quando vale a pena), valida e envia por SSH/rsync ao HP Mini (Debian), que só armazena e serve via File Browser. **Nunca converter no HP Mini.**

Idioma de trabalho: português (Brasil).

## Regras obrigatórias (do usuário)

- Não apagar, mover nem sobrescrever originais sem autorização explícita.
- Não formatar dispositivos. Nunca tocar na formatação do microSD.
- Não converter em massa antes de o usuário aprovar a qualidade visual.
- Não alterar File Browser, ZeroTier ou Debian sem necessidade comprovada.
- Sem Docker, VMs, IA residente ou dependências pesadas. Preferir Python 3, FFmpeg, rsync, SSH, launchd.
- Nunca usar `rsync --delete`. Nunca sobrescrever vídeo existente no destino.
- Não guardar senhas/chaves em scripts, JSON ou logs. Chave SSH dedicada, sem sobrescrever chaves existentes.
- Não considerar o projeto pronto sem teste real de ponta a ponta.
- Pedir aprovação antes de operações irreversíveis.

## Ambiente verificado (Mac, 2026-10-13)

- macOS 26.6.2, Apple M1 (arm64), 8 núcleos, 16 GB RAM, 255 GB livres.
- Python 3.14.8 (`/opt/homebrew/bin/python3`); FFmpeg/ffprobe 9.0.2 (`/opt/homebrew/bin`), com libx264, libvmaf, VideoToolbox.
- `/usr/bin/rsync` é **openrsync (protocolo 29)**, não o rsync completo: verificar opções suportadas antes de usar.
- OpenSSH 10.3; `launchctl` e `plutil` presentes. **Não existe `flock`**: usar `fcntl.flock` em Python.
- `~/.ssh/config` só tem o Include do OrbStack. Não há host `hpmini` nem chave dedicada ainda.
- Ping: ZeroTier (10.135.165.226) respondeu; IP local (192.168.15.202) não respondeu em 2026-10-13 (pode ser ICMP bloqueado, rede diferente ou servidor off). Reconfirmar por SSH.

## Servidor (informado pelo usuário em 2026-10-13, ainda NÃO verificado por mim)

- HP Mini 210-4000, Atom N2600 32 bits, 2 GB RAM, Debian 12 i386. Hostname `hpmini`, usuário `rick`.
- IP local `192.168.15.202`, ZeroTier `10.135.165.226` (rede HP-Mini-Videos). Nunca expor SSH na internet.
- File Browser v2.63.23 em `/usr/local/bin/filebrowser`, porta 8080, raiz `/srv/videos`, banco `/var/lib/hpmini-media/filebrowser.db`. Projeto arquivado: não ampliar exposição.
- microSD 256 GB: **exFAT, UUID `6ACE-6E04`, montado em `/srv/videos`, UID/GID 1000:1000**, automount via `/etc/fstab` testado após reboot. `filebrowser`, `zerotier-one`, `ssh` ativos após reboot.
- exFAT: sem permissões POSIX; não usar chown/chmod no cartão; cuidar de caracteres inválidos e nomes longos.

### Verificação obrigatória antes de CADA envio (`verify_remote_storage`)

1. SSH acessível (timeout).
2. `/srv/videos` é ponto de montagem real (`findmnt`).
3. Dispositivo montado tem UUID `6ACE-6E04` (nunca confiar em `/dev/sdb2`).
4. Filesystem é exfat.
5. Escrita permitida e espaço livre suficiente.
6. Caminho final não resolve para diretório inesperado.
Qualquer falha → abortar, manter arquivo local, reagendar. **Nunca usar o HD interno como fallback.**

## Resultado do protótipo FFmpeg (2026-10-13)

Vídeo: `~/Downloads/video.mp4` — 1080p30, H.264 5,2 Mbps, AAC, 94 s, 64,4 MB. **É uma pessoa falando, sem código: não serve para escolher o perfil.**

| Versão | Tamanho | vs. original | Tempo | VMAF |
|---|---|---|---|---|
| CRF 18 | 85,1 MB | +32% | 59 s | 96,8 |
| CRF 20 | 68,9 MB | +7% | 60 s | 96,0 |
| CRF 23 | 48,2 MB | −25% | 56 s | 94,2 |

Lições: fonte já H.264 em bitrate moderado → recomprimir com CRF ≤20 **aumenta** o arquivo. A regra `minimum_savings_percent = 15` (manter original se economizar menos) é essencial. Velocidade ≈ 1,6× tempo real no M1 (libx264, preset medium). Comando de referência usa `-map 0:v:0 -map '0:a:0?'` (aspas por causa do zsh).

## Arquitetura planejada

Estrutura (agora em `my-videos/` dentro do projeto, não em `~/VideoPipeline`): `incoming/ processing/ ready/ originals/ failed/ logs/ state/pipeline.sqlite3 scripts/ config/config.json tests/ docs/ launchd/`. Código-fonte fica neste repositório de trabalho e é instalado/linkado em `~/VideoPipeline`.

- Estados: DISCOVERED, WAITING_STABLE, ANALYZING, QUEUED, PROCESSING, VALIDATING, READY, UPLOAD_PENDING, UPLOADING, VERIFYING_REMOTE, COMPLETED, RETRY_PENDING, FAILED. Só COMPLETED após validar o arquivo remoto.
- Estabilidade de arquivo (tamanho+mtime) por 2–5 min antes de processar; extensões .mp4 .mov .mkv .m4v .webm; ignorar temporários.
- Perfis: quality (CRF18), balanced (CRF20, padrão), compact (CRF23), preset medium, libx264, yuv420p, AAC 128k, faststart, sem reduzir resolução/FPS. Decisão (converter / manter original / enviar direto) e streams registrados no SQLite.
- Upload: `ssh cat` retomável (sem rsync no servidor) para `nome.uploading` → verificar tamanho/SHA-256 → renomear no servidor → registrar. Preservar subpastas de `incoming/`. Nome existente e idêntico = já enviado; diferente = nome único ou parar.
- Concorrência: `fcntl.flock` em Python; um encode por vez. Retentativas com backoff.
- launchd: LaunchAgent `~/Library/LaunchAgents/com.hpmini.videopipeline.plist`, intervalo 900 s, sem root, sem processo residente. Funciona só com sessão do usuário ativa (documentar).
- CLI: `status`, `scan`, `run`, `retry`, `dry-run`. Logs com rotação, sem segredos.
- Host SSH: preferir IP local, cair para ZeroTier; chave dedicada + entrada em `~/.ssh/config`.

## Código (tudo nesta pasta)

- `scripts/video_pipeline.py` (pipeline, CLI), `config/config.json`, `launchd/*.plist` (template), `scripts/install.sh|uninstall.sh|healthcheck.sh`, `tests/test_pipeline.py` (32 testes, `python3 -m unittest discover -s tests`), `README.md` (raiz), `docs/TESTES.md`.
- **Pastas de trabalho ficam em `my-videos/` (`my-videos/incoming|processing|ready|originals|failed|logs|state`)** (`pipeline.home = "my-videos"`, relativo à raiz do projeto; ignoradas pelo git), por pedido do usuário. O usuário coloca vídeos (com subpastas) em `incoming/`; a estrutura de pastas é replicada em `/srv/videos`. Pastas vazias não são criadas no servidor.
- Remote é uma classe (`SSHRemote`) trocável; testes usam `FakeRemote`.

## Progresso por fase (atualizado 2026-10-13)

| Fase | Estado |
|---|---|
| 1 Inspeção | Feita |
| 2 Protótipo FFmpeg | Feita com vídeo de código: CRF 23 → −54% VMAF 96,0; perfil compact aprovado (APROVADO pelo usuário em 2026-10-13: compact/CRF 23, já no config) |
| 3 Pipeline local | Feita e testada (28 testes + execução real com ffmpeg) |
| 4 SSH | **Feita**: chave `~/.ssh/videopipeline_ed25519` autorizada; `status --remote` OK (UUID 6ACE-6E04, exfat, 238 GB livres) |
| 5 Upload real | **Testado com sucesso** em 2026-10-13: clipe 6,4 MB em `/srv/videos/_pipeline-teste/`, SHA-256 remoto = local. **O HP Mini não tem rsync**: envio é `ssh cat` retomável (nada instalado no servidor) |
| 6 LaunchAgent | Plist/instalador prontos; **NÃO carregado** |
| 7 Teste ponta a ponta | Não feito |

Nota: o ping ICMP ao IP local falha mas o SSH funciona; não usar ping como teste. Uma conexão `accept-new` adicionou 10.135.165.226 ao `~/.ssh/known_hosts`.

## Pendências

- Usuário: conferir `_pipeline-teste/` no File Browser (Mac e celular/ZeroTier) e apagar a pasta de teste se quiser. Existe também `/srv/videos/.pipeline-tmp/` (staging, oculta, deve ficar vazia).
- `install.sh --load` só com autorização do usuário, depois do teste real de upload; então testar reboot e HP Mini offline.

## Git

Repositório local (branch `main`), sem remote. `.gitignore` exclui `my-videos/*/*` (mantém `.gitkeep`), vídeos, `*.sqlite3`, logs e chaves. `config/config.json` É versionado (contém IPs da LAN/ZeroTier e UUID do microSD, nenhum segredo): não publicar em repositório público sem revisar.

## Decisões do usuário

- 2026-10-13: perfil `compact` (CRF 23) aprovado. `archive_originals_after_upload = true` (original é movido, nunca apagado, de `incoming/` para `originals/` após envio verificado).
