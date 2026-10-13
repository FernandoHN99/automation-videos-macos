import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import main as vp  # noqa: E402

BASE_CFG = json.loads((ROOT / "support" / "config.json").read_text())


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
            raise vp.Transient("rsync falhou")
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
        self.cfg["pipeline"]["mode"] = "upload"  # os testes antigos cobrem o envio por SSH (modo opcional)
        self.cfg["encoding"]["profiles"]["balanced"] = {"crf": 28, "preset": "ultrafast"}
        self.cfg["encoding"]["nice"] = 0
        self.remote = FakeRemote(self.tmp / "remote")
        self.p = vp.Pipeline(self.cfg, remote=self.remote)
        self.p.log.handlers[:] = [h for h in self.p.log.handlers if not isinstance(h, vp.logging.StreamHandler) or hasattr(h, "baseFilename")]
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
        self.assertTrue((self.remote.root / ("a_" + jobs[1]["sha256"][:8] + ".mp4")).exists() or jobs[1]["remote_rel"] != "a.mp4")

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
        self.assertEqual(self.jobs()[0]["encoding_time"] is not None, True)

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
