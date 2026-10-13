# VideoPipeline — contexto do projeto

Automação local no Mac. O usuário coloca vídeos (e pastas inteiras de aula) em `my-videos/incoming/`; o script comprime os vídeos com FFmpeg, copia os demais arquivos iguais e deixa tudo em `my-videos/ready/`, com a mesma estrutura de pastas. **O usuário envia `ready/` ao microSD do HP Mini manualmente** (arrastando). Toda conversão acontece no Mac, nunca no HP Mini.

Idioma de trabalho: português (Brasil). Leia o [README.md](README.md) para uso e configuração.

## Regras do usuário (valem sempre)

- Não apagar, mover nem sobrescrever originais sem autorização. (Autorizado: após ficar pronto, o original é **movido** de `incoming/` para `originals/`; nunca apagado.)
- Nunca formatar dispositivos nem tocar na formatação do microSD.
- Não alterar File Browser, ZeroTier ou Debian sem necessidade comprovada. Não instalar nada no servidor (ele não tem rsync; por isso o modo upload usa `ssh cat`).
- Sem Docker, VMs, IA residente ou dependências pesadas: só Python 3 (biblioteca padrão), FFmpeg, SSH, launchd.
- Nunca usar `--delete`; nunca sobrescrever arquivo de destino.
- Sem senhas ou chaves em scripts, JSON ou logs.
- Pedir aprovação antes de operações irreversíveis (ex.: apagar arquivos do servidor).

## Estado atual (2026-10-13)

- **Modo padrão: `pipeline.mode = "local"`.** Vídeos → comprimidos (libx264, perfil `compact` = CRF 23, resolução e FPS originais; se economizar < 15% copia o original). Outros arquivos (PDF, imagem, zip...) → copiados iguais, SHA-256 conferido, datas preservadas. Nada vai por SSH.
- **Modo `upload` (opcional, desligado):** envia vídeos ao HP Mini por `ssh cat` retomável, verificando UUID `6ACE-6E04`/exFAT/escrita/espaço antes de cada envio, SHA-256, `mv -n` atômico via `.pipeline-tmp/`. Testado de ponta a ponta com sucesso (clipe de 6,4 MB). Chave dedicada `~/.ssh/videopipeline_ed25519` já autorizada no servidor.
- **LaunchAgent `com.hpmini.videopipeline` carregado**: roda ao carregar e a cada 15 min, `/opt/homebrew/bin/python3`, sem root, sem processo residente. Só funciona com a sessão do usuário ativa.
- Perfil `compact` e `archive_originals_after_upload = true` aprovados pelo usuário.
- Primeiro teste real em andamento: `incoming/aula-01` (1 vídeo VP9 1080p de 2h11, 1,2 GB + ~230 arquivos de material). Um processamento por vez; os arquivos pequenos só são copiados depois do vídeo grande.
- Pendente: confirmar o resultado desse teste (`ready/`, `originals/`, `incoming/` vazio de arquivos processados); testar reboot do Mac; teste com o servidor desligado só se o modo upload for usado.

## Estrutura

```
main.py                      pipeline + CLI (arquivo único)
support/config/config.json   configuração
support/launchd/             modelo do plist
support/scripts/             install.sh (--load ativa), uninstall.sh, healthcheck.sh
support/tests/               40 testes: python3 -m unittest discover -s support/tests
my-videos/                   dados, ignorados pelo git (.gitkeep mantém a estrutura)
  incoming/ processing/ ready/ originals/ logs/ state/
```

- Estado e histórico: `my-videos/state/pipeline.sqlite3` (não apagar: evita reprocessar).
- Estados dos jobs: WAITING_STABLE, QUEUED, PROCESSING, COMPLETED, FAILED, SUPERSEDED (+ UPLOAD_PENDING, UPLOADING no modo upload). Retentativas por `next_attempt` com backoff 5 min → 6 h.
- Colunas `upload_path`/`remote_rel` do SQLite guardam, no modo local, o caminho do arquivo em `ready/` (nomes herdados do modo upload).
- Nomes de saída são ajustados para exFAT (`" * : < > ? \ |` viram `_`, Unicode NFC).
- Lock com `fcntl.flock` em `my-videos/state/pipeline.lock` (macOS não tem `flock`).
- `SSHRemote` é uma classe trocável; os testes usam um `FakeRemote`.

## Ambiente (verificado)

- Mac: macOS 26.6.2, Apple M1, 16 GB. Python 3.14, FFmpeg 9.0.2 (libx264, libvmaf, VideoToolbox). `/usr/bin/rsync` é openrsync (não usado).
- Servidor: HP Mini 210-4000, Debian 12 i386, usuário `rick`, IP local `192.168.15.202`, ZeroTier `10.135.165.226`. File Browser em `:8080` com raiz `/srv/videos` (microSD exFAT, UUID `6ACE-6E04`, automount via fstab). O ping ICMP ao IP local falha mas o SSH funciona.
- Servidor hoje: `video.mp4` e `_pipeline-teste/` (arquivos de teste do usuário/nossos) e a pasta oculta `.pipeline-tmp/` (área temporária do modo upload, vazia, só recriada se o modo upload for usado; pode ser apagada).

## Medições de qualidade

Screencast de programação (1080p, trecho de 2 min): CRF 18 → −30%, CRF 20 → −40%, CRF 23 → −54% (VMAF 96,9 / 96,6 / 96,0); texto legível no CRF 23. Vídeo de pessoa falando (5,2 Mbps): CRF 18/20 ficaram **maiores** que o original; por isso a regra dos 15%. Detalhes no README.

## Git

Repositório local, branch `main`, sem remote. `support/config/config.json` é versionado (IPs da LAN/ZeroTier e UUID do microSD, nenhum segredo): revisar antes de publicar.
