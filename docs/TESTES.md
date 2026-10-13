# Registro de testes

## 2026-10-13 — Qualidade (vídeo com código)

Fonte: `aula-05-back-end-com-graphql.mp4` (1080p30, H.264 0,85 Mbps, 17 min). Trecho de 2 min a partir de 5:00; quadros de comparação conferidos visualmente e depois descartados.

| Versão | Tamanho | Economia | VMAF | Tempo |
|---|---|---|---|---|
| Original (trecho, `-c copy`) | 14,7 MB | n/a | n/a | n/a |
| CRF 18 | 10,3 MB | 30% | 96,9 | 13 s |
| CRF 20 | 8,8 MB | 40% | 96,6 | 14 s |
| CRF 23 | 6,7 MB | 54% | 96,0 | 13 s |

Texto de código/terminal/árvore de arquivos legível no CRF 23 em inspeção visual (quadro 60 s). **Perfil definitivo: compact (CRF 23), aprovado pelo usuário após ver o resultado no File Browser.**

Vídeo sem código (`~/Downloads/video.mp4`, 5,2 Mbps): CRF 18/20 ficaram **maiores** que o original (+32%/+7%); só CRF 23 economizou (−25%). Motiva a regra de 15%.

## 2026-10-13 — Testes automatizados

28 testes OK (`tests/test_pipeline.py`): arquivo novo/já processado/alterado, em transferência, sem áudio, espaços/acentos/caracteres inválidos, mkv→mp4, corrompido, sem economia, falha do ffmpeg, SSH fora, microSD desmontado, sem espaço, nome existente (idêntico/diferente), hash divergente, recuperação (PROCESSING/UPLOADING), retry, lock concorrente, lock não permanente, dry-run, UUID ausente.

## 2026-10-13 — Execução real local (CLI, ffmpeg real)

Clipe de 2 min em `incoming/GraphQL/`: detectado, 1920x1080 preservado, CRF 23 → 14,0 → 6,4 MB (54,3%), validação OK, upload ficou `UPLOAD_PENDING` com backoff (SSH sem chave ainda). Original intacto.

## Pendente

- Upload real ao HP Mini (depende de autorizar a chave SSH).
- Verificação real de `verify_storage` contra o servidor.
- Teste ponta a ponta com LaunchAgent, reboot do Mac e HP Mini indisponível.
