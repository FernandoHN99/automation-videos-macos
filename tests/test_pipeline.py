import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main as vp  # noqa: E402

BASE_CFG = json.loads((ROOT / "config" / "config.json").read_text())


def make_video(path, crf=12, audio=True, seconds=2, size="320x240"):
    """Vídeo sintético minúsculo; crf baixo = fonte 'gorda' (permite economia ao recomprimir)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", str(crf), "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


class FakeRemote:
    """Substitui o HP Mini por um diretório local, com falhas injetáveis."""

    def __init__(self, root):
        self.root, self.host = Path(root), "fake"
        self.down = self.unmounted = self.nospace = self.corrupt_send = False
        self.sends = 0

    def connect(self):
        if self.down:
            raise vp.Transient("HP Mini inacessível")
        return "fake"

    def verify_storage(self, needed):
        if self.unmounted:
            raise vp.Transient("verificação do microSD falhou: /srv/videos NÃO é ponto de montagem")
        if self.nospace:
            raise vp.Transient("verificação do microSD falhou: espaço insuficiente")
        return {}

    def stat(self, rel):
        p = self.root / rel
        return (p.stat().st_size, vp.sha256_file(p)) if p.is_file() else None

    def send(self, local, jid):
        if self.down:
            raise vp.Transient("envio falhou")
        self.sends += 1
        dst = self.root / vp.STAGING / f"{jid}.part"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local, dst)
        if self.corrupt_send:
            with open(dst, "ab") as f:
                f.write(b"x")

    def staged(self, jid):
        return self.stat(f"{vp.STAGING}/{jid}.part")

    def publish(self, jid, rel):
        dst = self.root / rel
        if dst.exists():
            raise vp.Transient("destino já existe")
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(self.root / vp.STAGING / f"{jid}.part", dst)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = copy.deepcopy(BASE_CFG)
        self.cfg["pipeline"]["home"] = str(self.tmp / "home")
        self.cfg["pipeline"]["file_stability_seconds"] = 0
        self.cfg["pipeline"].update({"prevent_sleep": False, "wait_for_stable": False, "poll_seconds": 1})  # sem esperas nem caffeinate
        self.cfg["pipeline"]["mode"] = "upload"  # os testes antigos cobrem o envio por SSH (modo opcional)
        self.cfg["encoding"]["default_profile"] = "balanced"  # perfil rápido só para os testes (o padrão real é compact)
        self.cfg["encoding"]["profiles"]["balanced"] = {"crf": 28, "preset": "ultrafast"}
        self.cfg["encoding"]["nice"] = 0
        self.remote = FakeRemote(self.tmp / "remote")
        self.p = vp.Pipeline(self.cfg, remote=self.remote)
        # silencia o log no terminal durante os testes (mantém só os handlers de arquivo)
        self.p.log.handlers[:] = [h for h in self.p.log.handlers if hasattr(h, "baseFilename")]
        self.inc = self.p.d["incoming"]

    def tearDown(self):
        self.p.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cycle(self):
        # cada ciclo = uma execução do launchd; zera backoff para o teste não esperar
        self.p.db.execute("UPDATE jobs SET next_attempt=0"); self.p.db.commit()
        self.p.run_once()

    def jobs(self):
        return [dict(r) for r in self.p.db.execute("SELECT * FROM jobs ORDER BY id")]

    def state_of(self, i=0):
        return self.jobs()[i]["state"]


class TestHappyPath(Base):
    def test_novo_arquivo_comprime_envia_e_preserva_original(self):
        make_video(self.inc / "Python" / "Aula 01.mp4")
        orig = (self.inc / "Python" / "Aula 01.mp4").read_bytes()
        self.run_cycle()
        j = self.jobs()[0]
        self.assertEqual(j["state"], "COMPLETED")
        self.assertEqual(j["decision"], "encoded")
        self.assertGreater(j["savings_percent"], 15)
        self.assertTrue((self.remote.root / "Python" / "Aula 01.mp4").exists())
        self.assertEqual((self.p.d["originals"] / "Python" / "Aula 01.mp4").read_bytes(), orig)  # original intacto (arquivado)
        self.assertEqual(j["resolution"], "320x240")  # resolução preservada
        self.assertFalse(any((self.remote.root / vp.STAGING).glob("*.part")))

    def test_varios_videos_em_varias_pastas_preservam_estrutura(self):
        arquivos = ["Python/Aula 01.mp4", "Python/Avançado/Aula 02.mp4", "React/Hooks/Parte 1/a.mp4", "solto.mp4"]
        for a in arquivos:
            make_video(self.inc / a, seconds=1)
        self.run_cycle()
        for a in arquivos:
            self.assertTrue((self.remote.root / a).is_file(), a)
        self.assertEqual({j["state"] for j in self.jobs()}, {"COMPLETED"})
        self.assertEqual(self.remote.sends, 4)

    def test_original_arquivado_apos_envio_mantendo_pastas(self):
        self.cfg["pipeline"]["archive_originals_after_upload"] = True
        make_video(self.inc / "Python" / "Aula 01.mp4")
        orig = (self.inc / "Python" / "Aula 01.mp4").read_bytes()
        self.run_cycle()
        arq = self.p.d["originals"] / "Python" / "Aula 01.mp4"
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertEqual(arq.read_bytes(), orig)                       # original intacto, só movido
        self.assertFalse((self.inc / "Python" / "Aula 01.mp4").exists())
        self.run_cycle()
        self.assertEqual(len(self.jobs()), 1)                          # não reprocessa

    def test_original_nao_e_arquivado_se_envio_falhou(self):
        self.cfg["pipeline"]["archive_originals_after_upload"] = True
        make_video(self.inc / "a.mp4"); self.remote.unmounted = True; self.run_cycle()
        self.assertTrue((self.inc / "a.mp4").exists())
        self.assertEqual(list(self.p.d["originals"].rglob("*.mp4")), [])

    def test_arquivamento_nunca_sobrescreve(self):
        self.cfg["pipeline"]["archive_originals_after_upload"] = True
        (self.p.d["originals"]).mkdir(exist_ok=True); (self.p.d["originals"] / "a.mp4").write_bytes(b"antigo")
        make_video(self.inc / "a.mp4"); self.run_cycle()
        self.assertEqual((self.p.d["originals"] / "a.mp4").read_bytes(), b"antigo")
        self.assertTrue((self.p.d["originals"] / "a (1).mp4").exists())

    def test_arquivo_ja_processado_nao_repete(self):
        make_video(self.inc / "a.mp4"); self.run_cycle()
        self.run_cycle(); self.run_cycle()
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(self.remote.sends, 1)

    def test_arquivo_alterado_vira_novo_job(self):
        make_video(self.inc / "a.mp4"); self.run_cycle()
        make_video(self.inc / "a.mp4", seconds=3); self.run_cycle()
        jobs = self.jobs()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[1]["state"], "COMPLETED")
        self.assertEqual(jobs[1]["remote_rel"], f"a_{jobs[1]['sha256'][:8]}.mp4")  # versão nova não sobrescreve a antiga
        self.assertTrue((self.remote.root / "a.mp4").exists() and (self.remote.root / jobs[1]["remote_rel"]).exists())

    def test_video_sem_audio(self):
        make_video(self.inc / "mudo.mp4", audio=False); self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertIsNone(self.jobs()[0]["audio_codec"])

    def test_nome_com_espacos_e_acentos(self):
        make_video(self.inc / "Programação" / "Introdução à API — parte 1.mp4"); self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertTrue((self.remote.root / "Programação" / "Introdução à API — parte 1.mp4").exists())

    def test_nome_com_caracteres_invalidos_no_exfat(self):
        make_video(self.inc / "Aula: o que é?.mp4"); self.run_cycle()
        self.assertTrue((self.remote.root / "Aula_ o que é_.mp4").exists())

    def test_mkv_vira_mp4(self):
        make_video(self.inc / "x.mp4"); os.rename(self.inc / "x.mp4", self.inc / "x.mkv")
        self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertTrue((self.remote.root / "x.mp4").exists())


class TestEntrada(Base):
    def test_arquivo_em_transferencia_e_ignorado(self):
        self.cfg["pipeline"]["file_stability_seconds"] = 3600
        make_video(self.inc / "novo.mp4"); self.run_cycle()
        self.assertEqual(self.state_of(), "WAITING_STABLE")
        self.assertEqual(self.remote.sends, 0)

    def test_temporarios_sao_ignorados(self):
        (self.inc / "x.mp4.part").write_bytes(b"123"); (self.inc / "y.crdownload").write_bytes(b"1")
        (self.inc / ".oculto.mp4").write_bytes(b"1"); (self.inc / "doc.txt").write_text("x")
        self.run_cycle()
        self.assertEqual(self.jobs(), [])

    def test_arquivo_corrompido_falha_sem_perder_original(self):
        bad = self.inc / "ruim.mp4"; bad.write_bytes(os.urandom(5000)); self.run_cycle()
        for _ in range(3):
            self.run_cycle()
        self.assertEqual(self.state_of(), "FAILED")
        self.assertTrue(bad.exists())
        self.assertEqual(self.remote.sends, 0)

    def test_sem_economia_mantem_original(self):
        make_video(self.inc / "magro.mp4", crf=45); magro = (self.inc / "magro.mp4").read_bytes()
        self.cfg["encoding"]["profiles"]["balanced"] = {"crf": 10, "preset": "ultrafast"}
        self.run_cycle()
        j = self.jobs()[0]
        self.assertEqual((j["state"], j["decision"]), ("COMPLETED", "keep_original"))
        self.assertEqual((self.remote.root / "magro.mp4").read_bytes(), magro)

    def test_falha_do_ffmpeg_preserva_original(self):
        make_video(self.inc / "a.mp4")
        self.p.ffmpeg = "/usr/bin/false"
        for _ in range(3):
            self.run_cycle()
        self.assertEqual(self.state_of(), "FAILED")
        self.assertTrue((self.inc / "a.mp4").exists())
        self.assertEqual(list(self.p.d["processing"].glob("*")), [])


class TestRemoto(Base):
    def test_ssh_fora_mantem_upload_pendente_e_recupera(self):
        make_video(self.inc / "a.mp4")
        self.remote.down = True; self.run_cycle()
        self.assertEqual(self.state_of(), "UPLOAD_PENDING")
        self.assertTrue(Path(self.jobs()[0]["upload_path"]).exists())
        self.remote.down = False; self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertIsNotNone(self.jobs()[0]["encoding_time"])

    def test_nao_reconverte_quando_so_o_envio_falhou(self):
        make_video(self.inc / "a.mp4"); self.remote.down = True; self.run_cycle()
        calls = []; orig = self.p.build_cmd
        self.p.build_cmd = lambda *a, **k: calls.append(1) or orig(*a, **k)
        self.remote.down = False; self.run_cycle()
        self.assertEqual(calls, [])

    def test_microsd_desmontado_bloqueia_upload(self):
        make_video(self.inc / "a.mp4"); self.remote.unmounted = True; self.run_cycle()
        self.assertEqual(self.state_of(), "UPLOAD_PENDING")
        self.assertIn("ponto de montagem", self.jobs()[0]["error"])
        self.assertEqual(self.remote.sends, 0)

    def test_sem_espaco_remoto_bloqueia_sem_apagar(self):
        make_video(self.inc / "a.mp4"); self.remote.nospace = True; self.run_cycle()
        self.assertEqual(self.state_of(), "UPLOAD_PENDING")
        self.assertEqual(list(self.remote.root.rglob("*.mp4")), [])

    def test_backoff_impede_retentativa_imediata(self):
        make_video(self.inc / "a.mp4"); self.remote.down = True; self.run_cycle()
        self.remote.down = False
        self.p.run_once()  # sem zerar next_attempt: deve esperar
        self.assertEqual(self.state_of(), "UPLOAD_PENDING")

    def test_nome_existente_identico_nao_reenvia(self):
        make_video(self.inc / "a.mp4"); self.run_cycle()
        self.p.db.execute("UPDATE jobs SET state='UPLOAD_PENDING'"); self.p.db.commit()
        # recria o arquivo local a enviar, igual ao remoto
        j = self.jobs()[0]; shutil.copy(self.remote.root / "a.mp4", j["upload_path"])
        self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertEqual(self.remote.sends, 1)

    def test_nome_existente_diferente_nao_sobrescreve(self):
        (self.remote.root).mkdir(parents=True, exist_ok=True)
        (self.remote.root / "a.mp4").write_bytes(b"outro conteudo importante")
        make_video(self.inc / "a.mp4"); self.run_cycle()
        self.assertEqual((self.remote.root / "a.mp4").read_bytes(), b"outro conteudo importante")
        j = self.jobs()[0]
        self.assertEqual(j["state"], "COMPLETED")
        self.assertRegex(j["remote_rel"], r"^a_[0-9a-f]{8}\.mp4$")

    def test_hash_remoto_divergente_nao_publica(self):
        make_video(self.inc / "a.mp4"); self.remote.corrupt_send = True; self.run_cycle()
        self.assertEqual(self.state_of(), "UPLOAD_PENDING")
        self.assertIn("divergente", self.jobs()[0]["error"])
        self.assertEqual(list(self.remote.root.glob("*.mp4")), [])


class TestRecuperacao(Base):
    def test_job_interrompido_no_processing_volta_para_fila(self):
        make_video(self.inc / "a.mp4"); self.p.scan()
        jid = self.jobs()[0]["id"]
        self.p.set(jid, state="PROCESSING")
        (self.p.d["processing"] / f"{jid}.mp4").write_bytes(b"lixo parcial")
        self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")
        self.assertTrue((self.p.d["originals"] / "a.mp4").exists())  # original preservado (arquivado)

    def test_job_interrompido_no_uploading_continua(self):
        make_video(self.inc / "a.mp4"); self.remote.down = True; self.run_cycle()
        self.p.set(self.jobs()[0]["id"], state="UPLOADING"); self.remote.down = False
        self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")

    def test_retry_reenfileira_falhas(self):
        make_video(self.inc / "a.mp4"); self.p.ffmpeg = "/usr/bin/false"
        for _ in range(3):
            self.run_cycle()
        self.assertEqual(self.state_of(), "FAILED")
        self.p.ffmpeg = "ffmpeg"; self.assertEqual(self.p.retry(), 1)
        self.run_cycle()
        self.assertEqual(self.state_of(), "COMPLETED")


class TestConcorrenciaEDryRun(Base):
    def test_duas_execucoes_simultaneas(self):
        import fcntl
        make_video(self.inc / "a.mp4")
        held = open(self.p.d["state"] / "pipeline.lock", "w")  # outra execução segurando o lock
        fcntl.flock(held, fcntl.LOCK_EX)
        self.assertFalse(self.p.run_once())          # sai sem fazer nada
        self.assertEqual(self.jobs(), [])
        fcntl.flock(held, fcntl.LOCK_UN); held.close()
        self.assertTrue(self.p.run_once())           # lock livre: roda normalmente
        self.assertEqual(self.state_of(), "COMPLETED")

    def test_lock_nao_e_permanente_apos_falha(self):
        make_video(self.inc / "a.mp4")
        self.p.process = lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self.p.run_once()
        self.assertTrue(vp.Pipeline(self.cfg, remote=self.remote).run_once())

    def test_dry_run_nao_altera_nada(self):
        make_video(self.inc / "a.mp4")
        p = vp.Pipeline(self.cfg, remote=self.remote, dry_run=True)
        p.scan()
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.remote.sends, 0)
        p.db.close()


class TestModoLocal(Base):
    """Modo padrão: comprime vídeos e copia o resto para ready/; o envio ao HP Mini é manual."""

    def setUp(self):
        super().setUp()
        self.cfg["pipeline"]["mode"] = "local"
        self.ready = self.p.d["ready"]

    def test_video_comprimido_e_outros_arquivos_copiados_iguais(self):
        make_video(self.inc / "aula-01" / "aula.mp4")
        pdf = self.inc / "aula-01" / "material" / "Apostila - Aula 1.pdf"
        pdf.parent.mkdir(parents=True); pdf.write_bytes(os.urandom(5000))
        os.utime(pdf, (1_700_000_000, 1_700_000_000))
        zipf = self.inc / "aula-01" / "materiais.zip"; zipf.write_bytes(os.urandom(3000))
        self.run_cycle()
        self.assertEqual({j["state"] for j in self.jobs()}, {"COMPLETED"})
        self.assertTrue((self.ready / "aula-01" / "aula.mp4").is_file())
        self.assertLess((self.ready / "aula-01" / "aula.mp4").stat().st_size, 0.85 * (self.p.d["originals"] / "aula-01" / "aula.mp4").stat().st_size)
        self.assertEqual((self.ready / "aula-01" / "material" / "Apostila - Aula 1.pdf").read_bytes(), (self.p.d["originals"] / "aula-01" / "material" / "Apostila - Aula 1.pdf").read_bytes())
        self.assertEqual(int((self.ready / "aula-01" / "material" / "Apostila - Aula 1.pdf").stat().st_mtime), 1_700_000_000)  # data preservada
        self.assertTrue((self.ready / "aula-01" / "materiais.zip").is_file())

    def test_pastas_vazias_do_incoming_sao_removidas(self):
        make_video(self.inc / "aula" / "mod1" / "a.mp4", seconds=1)
        (self.inc / "aula" / "mod1" / ".DS_Store").write_bytes(b"x")
        (self.inc / "aula" / "vazia" / "sub").mkdir(parents=True)
        self.run_cycle()
        self.assertEqual([d.name for d in self.inc.iterdir() if not d.name.startswith(".")], [])  # tudo limpo
        self.assertTrue(self.inc.is_dir())                                                          # a raiz nunca some

    def test_pasta_some_assim_que_o_ultimo_arquivo_e_arquivado(self):
        self.p.prune_empty_dirs = lambda: None  # desliga a varredura: só a limpeza imediata pode remover
        (self.inc / "a" / "b" / "c").mkdir(parents=True)
        (self.inc / "a" / "b" / "c" / "x.pdf").write_bytes(b"1")
        (self.inc / "a" / "b" / "c" / ".DS_Store").write_bytes(b"x")
        (self.inc / "a" / "b" / "y.pdf").write_bytes(b"2")
        (self.inc / "outra").mkdir(); (self.inc / "outra" / "parcial.mp4.part").write_bytes(b"3")  # temporário: não é processado
        self.run_cycle()
        self.assertFalse((self.inc / "a").exists())                       # a, a/b e a/b/c esvaziaram e sumiram
        self.assertTrue((self.p.d["originals"] / "a" / "b" / "c" / "x.pdf").exists())
        self.assertTrue((self.inc / "outra" / "parcial.mp4.part").exists())  # pasta com conteúdo pendente é mantida
        self.assertTrue(self.inc.is_dir())

    def test_pasta_com_arquivo_ainda_nao_processado_nao_e_removida(self):
        self.p.prune_empty_dirs = lambda: None
        (self.inc / "a").mkdir()
        (self.inc / "a" / "pronto.pdf").write_bytes(b"1")
        make_video(self.inc / "a" / "video.mp4", seconds=1)
        real = self.p.process
        def so_o_pdf(job):  # simula o vídeo ainda pendente: só processa o pdf
            if job["src_rel"].endswith(".pdf"):
                real(job)
        self.p.process = so_o_pdf
        self.run_cycle()
        self.assertTrue((self.inc / "a" / "video.mp4").exists())          # pasta continua, pois ainda tem o vídeo
        self.assertFalse((self.inc / "a" / "pronto.pdf").exists())

    def test_pastas_vazias_antigas_somem_durante_a_fila_e_nao_no_fim(self):
        (self.inc / "velha" / "a" / "b").mkdir(parents=True); (self.inc / "velha" / "c").mkdir()
        (self.inc / "velha" / "c" / ".DS_Store").write_bytes(b"x")
        make_video(self.inc / "x.mp4", seconds=1); make_video(self.inc / "y.mp4", seconds=1)
        seen, real = [], self.p.process
        def espia(job):
            seen.append((self.inc / "velha").exists()); real(job)
        self.p.process = espia
        self.run_cycle()
        self.assertEqual(seen, [False, False])  # já tinha sumido antes do 1º vídeo (não só ao terminar a fila)

    def test_pasta_vazia_recente_ou_com_arquivo_pendente_e_mantida(self):
        self.cfg["pipeline"]["file_stability_seconds"] = 3600
        (self.inc / "copiando" / "sub").mkdir(parents=True)      # pasta nova: usuário pode estar copiando
        make_video(self.inc / "outra" / "a.mp4", seconds=1)      # ainda não estável: continua na fila
        self.run_cycle()
        self.assertTrue((self.inc / "copiando" / "sub").is_dir())
        self.assertTrue((self.inc / "outra" / "a.mp4").exists())

    def test_arquivos_que_nao_sao_video_vem_antes_dos_videos(self):
        make_video(self.inc / "aula" / "a-video.mp4", seconds=1)       # primeiro em ordem alfabética
        (self.inc / "aula" / "b-material.pdf").write_bytes(b"pdf")
        make_video(self.inc / "aula" / "c-video.mp4", seconds=1)
        (self.inc / "aula" / "d-planilha.xlsx").write_bytes(b"xls")
        ordem, orig = [], self.p.process
        self.p.process = lambda job: (ordem.append(job["src_rel"]), orig(job))[1]
        self.run_cycle()
        self.assertEqual(ordem, ["aula/b-material.pdf", "aula/d-planilha.xlsx", "aula/a-video.mp4", "aula/c-video.mp4"])

    def test_nao_envia_nada_ao_servidor(self):
        make_video(self.inc / "a.mp4"); (self.inc / "x.pdf").write_bytes(b"abc")
        self.remote.down = True  # se tentasse conectar, falharia
        self.run_cycle()
        self.assertEqual(self.remote.sends, 0)
        self.assertEqual({j["state"] for j in self.jobs()}, {"COMPLETED"})
        self.assertEqual(list(self.remote.root.rglob("*")) if self.remote.root.exists() else [], [])

    def test_original_vai_para_originals_e_incoming_esvazia(self):
        make_video(self.inc / "a.mp4"); (self.inc / "b.txt").write_text("oi"); self.run_cycle()
        self.assertEqual([f.name for f in self.inc.rglob("*") if f.is_file() and not f.name.startswith(".")], [])
        self.assertTrue((self.p.d["originals"] / "a.mp4").exists() and (self.p.d["originals"] / "b.txt").exists())

    def test_ocultos_e_temporarios_ignorados(self):
        for n in (".DS_Store", "._a.mp4", "x.mp4.part", "y.crdownload"):
            (self.inc / n).write_bytes(b"1")
        (self.inc / ".oculta").mkdir(); (self.inc / ".oculta" / "z.pdf").write_bytes(b"1")
        self.run_cycle()
        self.assertEqual(self.jobs(), [])

    def test_sem_economia_copia_original_para_ready(self):
        make_video(self.inc / "magro.mp4", crf=45); magro = (self.inc / "magro.mp4").read_bytes()
        self.cfg["encoding"]["profiles"]["balanced"] = {"crf": 10, "preset": "ultrafast"}
        self.run_cycle()
        j = self.jobs()[0]
        self.assertEqual((j["state"], j["decision"]), ("COMPLETED", "keep_original"))
        self.assertEqual((self.ready / "magro.mp4").read_bytes(), magro)

    def test_nunca_sobrescreve_em_ready(self):
        self.ready.mkdir(exist_ok=True); (self.ready / "x.pdf").write_bytes(b"antigo diferente")
        (self.inc / "x.pdf").write_bytes(b"novo"); self.run_cycle()
        self.assertEqual((self.ready / "x.pdf").read_bytes(), b"antigo diferente")
        self.assertEqual((self.ready / "x (1).pdf").read_bytes(), b"novo")

    def test_idempotente(self):
        (self.inc / "x.pdf").write_bytes(b"novo"); make_video(self.inc / "a.mp4")
        self.run_cycle(); self.run_cycle(); self.run_cycle()
        self.assertEqual(len(self.jobs()), 2)

    def test_nome_com_caractere_invalido_no_exfat_e_ajustado(self):
        (self.inc / "Aula: parte 1?.pdf").write_bytes(b"x"); self.run_cycle()
        self.assertTrue((self.ready / "Aula_ parte 1_.pdf").exists())


class TestExecucaoManual(Base):
    def setUp(self):
        super().setUp()
        self.cfg["pipeline"]["mode"] = "local"

    def test_espera_arquivo_recem_copiado_ficar_estavel(self):
        self.cfg["pipeline"].update({"wait_for_stable": True, "file_stability_seconds": 2, "poll_seconds": 1})
        (self.inc / "novo.pdf").write_bytes(b"acabou de chegar")   # mtime = agora: ainda não está estável
        self.p.run_once()                                          # uma única execução deve esperar e concluir
        self.assertEqual(self.state_of(), "COMPLETED")

    def test_espera_ignora_arquivo_que_sumiu(self):
        self.cfg["pipeline"].update({"wait_for_stable": True, "file_stability_seconds": 3600})
        (self.inc / "x.pdf").write_bytes(b"1"); self.p.scan()
        (self.inc / "x.pdf").unlink()                              # usuário desistiu: não pode travar a execução
        self.assertEqual(self.p.waiting_for_stable(), 0)
        self.assertEqual(self.state_of(), "SUPERSEDED")

    def test_caffeinate_preso_ao_pid_e_sem_duplicar(self):
        from unittest import mock
        self.cfg["pipeline"]["prevent_sleep"] = True
        with mock.patch.object(vp.shutil, "which", return_value="/usr/bin/caffeinate"), \
             mock.patch.object(vp.subprocess, "Popen") as popen:
            self.p.keep_awake(); self.p.keep_awake()
            popen.assert_called_once_with(["caffeinate", "-i", "-m", "-s", "-w", str(os.getpid())])
            self.p.release_awake()
            popen.return_value.terminate.assert_called_once()

    def test_caffeinate_desligado_por_config(self):
        from unittest import mock
        self.cfg["pipeline"]["prevent_sleep"] = False
        with mock.patch.object(vp.subprocess, "Popen") as popen:
            self.p.keep_awake()
            popen.assert_not_called()

    def test_lock_registra_pid_para_status_e_stop(self):
        import fcntl
        visto = []
        orig = self.p.process
        self.p.process = lambda job: (visto.append(self.p.running_pid()), orig(job))[1]
        (self.inc / "x.pdf").write_bytes(b"1"); self.run_cycle()
        self.assertEqual(visto, [os.getpid()])  # durante a execução o lock guarda o nosso PID (status/stop o encontram)
        self.assertIsNone(self.p.running_pid())  # terminou: lock livre
        held = open(self.p.d["state"] / "pipeline.lock", "a+")
        fcntl.flock(held, fcntl.LOCK_EX); held.seek(0); held.truncate(); held.write("12345"); held.flush()
        self.assertEqual(self.p.running_pid(), 12345)
        fcntl.flock(held, fcntl.LOCK_UN); held.close()
        self.assertIsNone(self.p.running_pid())


class TestPainel(Base):
    """Progresso ao vivo do ffmpeg, cópia com SHA único e dados do painel (somente leitura)."""

    def setUp(self):
        super().setUp()
        self.cfg["pipeline"]["mode"] = "local"

    def test_parse_progress(self):
        cur = {}
        for line in ("out_time_us=1500000\n", "speed=3.2x\n", "progress=continue\n"):
            vp.parse_progress(line, cur)
        self.assertEqual((cur["out_time"], cur["speed"], cur["done"]), (1.5, 3.2, False))
        vp.parse_progress("speed=N/A\n", cur); vp.parse_progress("progress=end\n", cur)
        self.assertEqual((cur["speed"], cur["done"]), (3.2, True))  # N/A não sobrescreve

    def test_order_queue_videos_por_ultimo(self):
        rows = [{"id": 1, "src_rel": "a.mp4"}, {"id": 2, "src_rel": "b.pdf"}, {"id": 3, "src_rel": "c.MOV"}, {"id": 4, "src_rel": "d.zip"}]
        self.assertEqual([r["id"] for r in vp.order_queue(rows, {".mp4", ".mov"})], [2, 4, 1, 3])

    def test_copy_hashed_igual_ao_sha256_file(self):
        src, dst = self.tmp / "o.bin", self.tmp / "c.bin"
        src.write_bytes(os.urandom(9_000_000)); os.utime(src, (1_700_000_000, 1_700_000_000))
        self.assertEqual(vp.copy_hashed(src, dst), vp.sha256_file(src))
        self.assertEqual(dst.read_bytes(), src.read_bytes())
        self.assertEqual(int(dst.stat().st_mtime), 1_700_000_000)

    def test_encode_devolve_rc_e_limpa_progress(self):
        make_video(self.inc / "a.mp4", seconds=2)
        info = vp.probe(self.inc / "a.mp4")
        out = self.p.d["processing"] / "1.mp4"
        cmd = self.p.build_cmd(self.inc / "a.mp4", out, info, self.p.profile()[1])
        self.assertIn("-progress", cmd)
        rc, err = self.p.encode(cmd, {"id": 1, "src_rel": "a.mp4", "size": 1}, info, out)
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.is_file())
        self.assertFalse((self.p.d["state"] / vp.PROGRESS).exists())
        rc, err = self.p.encode(cmd[:-1] + ["/nao/existe/x.mp4"], {"id": 1, "src_rel": "a.mp4", "size": 1}, info, out)
        self.assertNotEqual(rc, 0); self.assertTrue(err.strip())

    def test_painel_snapshot(self):
        make_video(self.inc / "aula" / "a.mp4")
        (self.inc / "aula" / "m.pdf").write_bytes(os.urandom(2000))
        self.p.run_once()  # histórico: 1 vídeo codificado + 1 PDF copiado
        make_video(self.inc / "aula" / "b.mp4")
        (self.inc / "z.pdf").write_bytes(b"x")
        self.p.scan(); self.p.db.execute("UPDATE jobs SET state='QUEUED' WHERE state='WAITING_STABLE'"); self.p.db.commit()
        snap = vp.DashboardData(self.cfg).snapshot()
        self.assertFalse(snap["running"])
        self.assertEqual((snap["done"]["count"], snap["done"]["copied"]), (2, 1))
        self.assertGreater(snap["done"]["saved"], 0)
        self.assertEqual([q["rel"] for q in snap["queue"]], ["z.pdf", "aula/b.mp4"])  # não-vídeo antes
        self.assertGreater(snap["queue"][1]["duration"], 0)
        self.assertGreater(snap["queue"][1]["est"], 0)
        self.assertGreater(snap["eta"]["seconds"], 0)
        self.assertEqual(snap["forecast"]["bytes"], snap["queue"][1]["size"])
        self.assertGreater(snap["forecast"]["saved"], 0)

    def test_painel_sem_banco(self):
        cfg = copy.deepcopy(self.cfg); cfg["pipeline"]["home"] = str(self.tmp / "vazio")
        snap = vp.DashboardData(cfg).snapshot()
        self.assertEqual((snap["queue"], snap["current"], snap["running"]), ([], None, False))

    def test_scan_ordem_e_ocultos(self):
        for rel in ("b/2.pdf", "a.pdf", "b/1.pdf", ".oculta/x.pdf", "b/.DS_Store"):
            (self.inc / rel).parent.mkdir(parents=True, exist_ok=True); (self.inc / rel).write_bytes(b"1")
        self.assertEqual([p.relative_to(self.inc).as_posix() for p in self.p.walk_incoming()], ["a.pdf", "b/1.pdf", "b/2.pdf"])


class TestPainelEscrita(Base):
    """Envio de arquivos, token e Host do painel (escrita só com token, só em loopback)."""

    def test_incoming_target_recusa_caminhos_perigosos(self):
        for bad in ("", "/etc/passwd", "../x.mp4", "a/../../x", "a//b", ".oculto/x.pdf", "a/.DS_Store", "a/\x00b"):
            with self.assertRaises(ValueError, msg=bad):
                vp.incoming_target(self.inc, bad)
        self.assertEqual(vp.incoming_target(self.inc, "aula%201/a%20b.mp4"), self.inc / "aula 1" / "a b.mp4")

    def test_save_upload_nao_sobrescreve_e_preserva_data(self):
        import io
        a = vp.save_upload(self.inc, "aula/x.pdf", io.BytesIO(b"um"), 2, 1_700_000_000_000)
        b = vp.save_upload(self.inc, "aula/x.pdf", io.BytesIO(b"dois"), 4)
        self.assertEqual((a, b), ("aula/x.pdf", "aula/x (1).pdf"))
        self.assertEqual((self.inc / a).read_bytes(), b"um")
        self.assertEqual(int((self.inc / a).stat().st_mtime), 1_700_000_000)
        self.assertEqual([p.name for p in (self.inc / "aula").iterdir() if p.name.endswith(".part")], [])
        with self.assertRaises(OSError):  # corpo menor que o Content-Length: nada fica
            vp.save_upload(self.inc, "aula/y.pdf", io.BytesIO(b"x"), 5)
        self.assertEqual(sorted(p.name for p in (self.inc / "aula").iterdir()), ["x (1).pdf", "x.pdf"])

    def test_servidor_exige_token_e_host(self):
        import threading, urllib.request, urllib.error, socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        threading.Thread(target=vp.serve_dashboard, args=(self.cfg, port), daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                html = urllib.request.urlopen(base + "/").read().decode(); break
            except OSError:
                time.sleep(0.1)
        token = html.split('name="vp-token" content="')[1].split('"')[0]
        self.assertNotIn("__TOKEN__", html)

        def post(path, body=b"", headers=None):
            req = urllib.request.Request(base + path, data=body, method="POST", headers=headers or {})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        self.assertEqual(post("/api/upload", b"x", {"X-Rel": "a.pdf"})[0], 403)  # sem token
        self.assertFalse((self.inc / "a.pdf").exists())
        ok = post("/api/upload", b"hello", {"X-VP-Token": token, "X-Rel": "pasta/a.pdf", "X-Mtime": "1700000000000"})
        self.assertEqual(ok, (200, {"saved": "pasta/a.pdf"}))
        self.assertEqual((self.inc / "pasta" / "a.pdf").read_bytes(), b"hello")
        self.assertEqual(post("/api/upload", b"x", {"X-VP-Token": token, "X-Rel": "../fora.pdf"})[0], 400)
        from unittest import mock
        with mock.patch.object(vp, "peek_pid", return_value=123):  # com o processamento rodando, nada é aceito
            self.assertEqual(post("/api/upload", b"x", {"X-VP-Token": token, "X-Rel": "b.pdf"})[0], 409)
        self.assertFalse((self.inc / "b.pdf").exists())
        self.assertEqual(post("/api/stop", b"", {"X-VP-Token": token})[1], {"stopped": None})  # nada rodando
        req = urllib.request.Request(base + "/api/status", headers={"Host": "evil.example"})  # DNS rebinding
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)


class TestAjustes(Base):
    """Parâmetros de compressão: o padrão não muda nada; ajustes do painel são validados e aplicados."""

    def setUp(self):
        super().setUp()
        self.cfg["pipeline"]["mode"] = "local"
        self.info = dict(width=1920, height=1080, fps=30.0, acodec="aac", vcodec="h264", duration=10.0)

    def cmd(self, **over):
        s = vp.clean_settings(over, vp.default_settings(self.cfg))
        return vp.build_ffmpeg_cmd(self.cfg, "in.mp4", "out.mp4", self.info, s)

    def test_padrao_gera_exatamente_o_comando_de_sempre(self):
        s = vp.default_settings(self.cfg)
        self.assertEqual((s["crf"], s["preset"], s["codec"]), (28, "ultrafast", "libx264"))  # perfil de teste do Base
        self.cfg["encoding"]["default_profile"] = "compact"  # o padrão real: CRF 23, medium
        legacy = ["nice", "-n", "0", "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", "in.mp4", "-map", "0:v:0", "-map", "0:a:0",
                  "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "copy", "-map_metadata", "0",
                  "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "out.mp4"]
        self.cfg["encoding"]["nice"] = 10
        self.assertEqual(self.cmd(), ["nice", "-n", "10"] + legacy[3:])
        self.info.update(acodec="mp3", width=1919)  # áudio não-AAC vira AAC 128k; dimensão ímpar ganha scale
        c = self.cmd()
        self.assertIn("scale=trunc(iw/2)*2:trunc(ih/2)*2", c); self.assertEqual(c[c.index("-c:a") + 1:c.index("-c:a") + 4], ["aac", "-b:a", "128k"])

    def test_ajustes_viram_flags(self):
        c = self.cmd(max_height=720, max_fps=24, audio="none", copy_metadata=False, tune="film", profile="high", faststart=False, threads=4, crf=18, preset="slow")
        for a, b in (("-crf", "18"), ("-preset", "slow"), ("-tune", "film"), ("-profile:v", "high"), ("-map_metadata", "-1"), ("-threads", "4"), ("-vf", "scale=-2:720,fps=24")):
            self.assertEqual(c[c.index(a) + 1], b, a)
        self.assertNotIn("-movflags", c); self.assertNotIn("-c:a", c); self.assertEqual(c.count("-map"), 1)  # sem áudio: só o mapa do vídeo
        h = self.cmd(codec="libx265", tune="grain"); self.assertIn("hvc1", h)
        v = self.cmd(codec="h264_videotoolbox", vt_quality=70); self.assertEqual(v[v.index("-q:v") + 1], "70"); self.assertNotIn("-crf", v)
        self.info["height"] = 480; self.assertNotIn("scale", " ".join(self.cmd(max_height=720)))  # nunca amplia

    def test_valores_perigosos_ou_invalidos_sao_recusados(self):
        base = vp.default_settings(self.cfg)
        for bad in ({"codec": "libx264; rm -rf /"}, {"crf": 99}, {"crf": "abc"}, {"crf": True}, {"preset": "-y"}, {"tune": "film", "codec": "libx265"},
                    {"profile": "high", "codec": "libx265"}, {"max_height": 999}, {"audio_bitrate": "1;"}, {"faststart": "sim"}, {"nome": 1}, {"threads": 99}):
            with self.assertRaises(ValueError, msg=str(bad)):
                vp.clean_settings(bad, base)
        self.assertEqual(vp.clean_settings({"crf": "20", "max_height": "720"}, base)["max_height"], 720)

    def test_encode_real_com_ajustes_redimensiona_remove_audio_e_registra(self):
        make_video(self.inc / "a.mp4", size="640x480")
        vp.save_settings(self.cfg, self.p.d["state"], {"max_height": 360, "audio": "none"})
        self.assertEqual(self.p.profile()[0], "custom")
        self.run_cycle()
        j = self.jobs()[0]
        self.assertEqual((j["state"], j["decision"], j["profile"]), ("COMPLETED", "encoded", "custom"), j["error"])
        out = vp.probe(self.p.d["ready"] / "a.mp4")
        self.assertEqual((out["width"], out["height"], out["acodec"]), (480, 360, None))
        self.assertEqual(json.loads(j["settings"])["max_height"], 360)

    def test_salvar_igual_ao_padrao_apaga_o_arquivo(self):
        f = self.p.d["state"] / vp.SETTINGS_FILE
        vp.save_settings(self.cfg, self.p.d["state"], {"crf": 30}); self.assertTrue(f.exists())
        vp.save_settings(self.cfg, self.p.d["state"], {}, reset=True); self.assertFalse(f.exists())
        self.assertEqual(self.p.profile()[0], "balanced")  # voltou ao padrão (nome do perfil do config)
        f.write_text("{lixo"); self.assertEqual(vp.effective_settings(self.cfg, self.p.d["state"]), vp.default_settings(self.cfg))  # arquivo corrompido = padrão

    def test_api_de_ajustes(self):
        import threading, urllib.request, urllib.error, socket
        from unittest import mock
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        threading.Thread(target=vp.serve_dashboard, args=(self.cfg, port), daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                html = urllib.request.urlopen(base + "/").read().decode(); break
            except OSError:
                time.sleep(0.1)
        token = html.split('name="vp-token" content="')[1].split('"')[0]

        def post(body, tok=token):
            req = urllib.request.Request(base + "/api/settings", data=json.dumps(body).encode(), method="POST", headers={"X-VP-Token": tok})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        g = json.loads(urllib.request.urlopen(base + "/api/settings").read())
        self.assertFalse(g["custom"]); self.assertIn("-crf", g["preview"]); self.assertNotIn("nice", g["preview"]); self.assertNotIn("-progress", g["preview"])
        self.assertEqual(post({"settings": {"crf": 30}}, "errado")[0], 403)
        code, r = post({"settings": {"crf": 30, "max_height": 720}})
        self.assertEqual((code, r["custom"], r["settings"]["crf"]), (200, True, 30)); self.assertIn("720", " ".join(r["description"]))
        self.assertEqual(post({"settings": {"crf": 99}})[0], 400)
        self.assertEqual(post({"settings": {"codec": "nao_existe"}})[0], 400)
        with mock.patch.object(vp, "peek_pid", return_value=1):
            self.assertEqual(post({"reset": True})[0], 409)
        self.assertTrue(json.loads(urllib.request.urlopen(base + "/api/settings").read())["custom"])  # o 409 não mexeu
        self.assertFalse(post({"reset": True})[1]["custom"])


class TestUtilidades(unittest.TestCase):
    def test_safe_rel(self):
        self.assertEqual(vp.safe_rel("a/b: c?.mp4"), "a/b_ c_.mp4")
        self.assertEqual(vp.safe_rel("fim com ponto. "), "fim com ponto")
        self.assertLessEqual(len(vp.safe_part("x" * 400 + ".mp4").encode()), 200)

    def test_storage_sem_uuid_bloqueia(self):
        cfg = copy.deepcopy(BASE_CFG); cfg["remote"]["expected_uuid"] = None
        r = vp.SSHRemote(cfg, vp.logging.getLogger("t"))
        with self.assertRaises(vp.Transient):
            r.verify_storage(1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
