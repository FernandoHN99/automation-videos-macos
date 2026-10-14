/** Estado do processamento: atualizar, iniciar e parar. */
export function createRunController({ api, store, ui }) {
  async function refresh() {
    try {
      store.set({ status: await api.status(), offline: false });
    } catch {
      store.set({ offline: true });
    }
  }

  async function startRun() {
    try {
      const { started } = await api.start();
      ui.toast(started ? "Processamento iniciado" : "Já estava rodando", "ok");
    } catch (error) {
      ui.toast(`Erro: ${error.message}`, "bad");
    }
    await refresh();
  }

  async function stopRun() {
    const confirmed = await ui.confirm({
      title: "Parar o processamento?",
      text: "O vídeo em andamento é interrompido e volta ao início da fila quando você iniciar de novo. Nada é perdido.",
      yes: "Parar",
      danger: true,
    });
    if (!confirmed) return;
    try {
      await api.stop();
      ui.toast("Processamento parado", "ok");
    } catch (error) {
      ui.toast(`Erro: ${error.message}`, "bad");
    }
    await refresh();
  }

  function showQueueTab(tab) {
    store.set({ queueTab: tab });
    document.getElementById("queue-panel")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  return { refresh, startRun, stopRun, showQueueTab };
}

/** Consulta o servidor a cada `every` ms enquanto a aba está visível. */
export function startPolling(refresh, every = 2000) {
  const tick = () => {
    if (!document.hidden) refresh();
  };
  setInterval(tick, every);
  document.addEventListener("visibilitychange", tick);
  tick();
}
