# VideoPipeline — contexto do projeto

Automação local no Mac. O usuário coloca vídeos (e pastas inteiras de aula) em `my-videos/incoming/`; o script comprime os vídeos com FFmpeg, copia os demais arquivos iguais e deixa tudo em `my-videos/ready/`, com a mesma estrutura de pastas. **O usuário envia `ready/` ao microSD do HP Mini manualmente** (arrastando). Toda conversão acontece no Mac, nunca no HP Mini.

Idioma de trabalho: português (Brasil). Leia o [README.md](README.md) para uso e configuração.

## Regras do usuário (valem sempre)

- Não apagar, mover nem sobrescrever originais sem autorização. (Autorizado: após ficar pronto, o original é **movido** de `incoming/` para `originals/`; nunca apagado.)
- Nunca formatar dispositivos nem tocar na formatação do microSD.
- Não alterar File Browser, ZeroTier ou Debian sem necessidade comprovada. Não instalar nada no servidor (ele não tem rsync; por isso o modo upload usa `ssh cat`).
- Sem Docker, VMs, IA residente ou dependências pesadas: só Python 3 (biblioteca padrão), FFmpeg, SSH (modo upload) e `caffeinate`.
- Nunca usar `--delete`; nunca sobrescrever arquivo de destino.
- Sem senhas ou chaves em scripts, JSON ou logs.
- Pedir aprovação antes de operações irreversíveis (ex.: apagar arquivos do servidor).

## Estado atual (2026-10-13)

- **Modo padrão: `pipeline.mode = "local"`.** Vídeos → comprimidos (libx264, perfil `compact` = CRF 23, resolução e FPS originais; se economizar < 15% copia o original). Outros arquivos (PDF, imagem, zip...) → copiados iguais, SHA-256 conferido, datas preservadas. Nada vai por SSH.
- **Modo `upload` (opcional, desligado):** envia vídeos ao HP Mini por `ssh cat` retomável, verificando UUID `6ACE-6E04`/exFAT/escrita/espaço antes de cada envio, SHA-256, `mv -n` atômico via `.pipeline-tmp/`. Testado de ponta a ponta com sucesso (clipe de 6,4 MB). Chave dedicada `~/.ssh/videopipeline_ed25519` já autorizada no servidor.
- **Execução manual, sem agendamento (decisão do usuário, 2026-10-13):** o LaunchAgent foi removido. `python3 main.py start` roda em segundo plano (nova sessão, sobrevive ao Terminal); `run` em primeiro plano; `stop` encerra (mata o grupo de processos, inclusive o ffmpeg); `status` mostra o PID. Enquanto há trabalho, o `caffeinate -i -m -s -w <pid>` bloqueia a suspensão (verificado com `pmset -g assertions`); fechar a tampa do MacBook sem monitor externo suspende mesmo assim. O `run` espera arquivos recém-copiados ficarem estáveis (`wait_for_stable`, `poll_seconds`) e só termina quando não resta nada. O PID fica em `my-videos/state/pipeline.lock`.
- Perfil `compact` e `archive_originals_after_upload = true` aprovados pelo usuário. Fluxo: validado/copiado → original vai de `incoming/` para `originals/`; `ready/` fica com os vídeos finais e os arquivos copiados; pastas vazias de `incoming/` são removidas (só as sem alteração há `file_stability_seconds`).
- **Teste real concluído (2026-10-13):** `incoming/aula-01` (vídeo VP9 1080p de 2h11, 1,2 GB → 828 MB H.264, −30,7%, 27 min; 236 materiais idênticos por SHA-256 e datas preservadas). `incoming/` ficou limpo.
- Pendente (do usuário): arrastar `ready/` para o microSD e conferir a qualidade da aula de 2h antes de apagar `originals/`.

## Estrutura

```
main.py                      pipeline + CLI (arquivo único)
support/config/config.json   configuração
support/scripts/             healthcheck.sh
support/tests/               48 testes: python3 -m unittest discover -s support/tests
my-videos/                   dados, ignorados pelo git (.gitkeep mantém a estrutura)
  incoming/ processing/ ready/ originals/ logs/ state/
```

- Estado e histórico: `my-videos/state/pipeline.sqlite3` (não apagar: evita reprocessar).
- Ordem da fila: arquivos que não são vídeo primeiro, vídeos por último (cada original vai para `originals/` assim que o seu arquivo fica pronto).
- Estados dos jobs: WAITING_STABLE, QUEUED, PROCESSING, COMPLETED, FAILED, SUPERSEDED (+ UPLOAD_PENDING, UPLOADING no modo upload). Retentativas por `next_attempt` com backoff 5 min → 6 h.
- Colunas `upload_path`/`remote_rel` do SQLite guardam, no modo local, o caminho do arquivo em `ready/` (nomes herdados do modo upload).
- Nomes de saída são ajustados para exFAT (`" * : < > ? \ |` viram `_`, Unicode NFC).
- Lock com `fcntl.flock` em `my-videos/state/pipeline.lock` (macOS não tem `flock`).
- `SSHRemote` é uma classe trocável; os testes usam um `FakeRemote`.

## Ambiente (verificado)

- Mac: macOS 26.6.2, Apple M1, 16 GB. Python 3.14, FFmpeg 9.0.2 (libx264, libvmaf, VideoToolbox). `/usr/bin/rsync` é openrsync (não usado).
- Servidor: HP Mini 210-4000, Debian 12 i386, usuário `rick`, IP local `192.168.15.202`, ZeroTier `10.135.165.226`. File Browser em `:8080` com raiz `/srv/videos` (microSD exFAT, UUID `6ACE-6E04`, automount via fstab). O ping ICMP ao IP local falha mas o SSH funciona.
- Servidor (última checagem 2026-10-13): `_pipeline-teste/` (vídeo do nosso teste) e a pasta oculta `.pipeline-tmp/` (área temporária do modo upload, vazia, só recriada se esse modo for usado; o usuário pode apagar ambas). O `video.mp4` antigo não está mais lá (não fomos nós).

## Medições de qualidade

Screencast de programação (1080p, trecho de 2 min): CRF 18 → −30%, CRF 20 → −40%, CRF 23 → −54% (VMAF 96,9 / 96,6 / 96,0); texto legível no CRF 23. Vídeo de pessoa falando (5,2 Mbps): CRF 18/20 ficaram **maiores** que o original; por isso a regra dos 15%. Detalhes no README.

## Git

Remote `origin`: https://github.com/FernandoHN99/automation-videos-macos (**PRIVADO**), branch `main`. `support/config/config.json` é versionado (IPs da LAN/ZeroTier, usuário e UUID do microSD, nenhum segredo): **não tornar o repositório público sem trocar esse arquivo por um exemplo** (o histórico também os contém).
