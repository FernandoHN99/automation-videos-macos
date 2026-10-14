/** Ajustes de compressão: carregar, salvar, restaurar o padrão e abrir/fechar a gaveta. */
export function createSettingsController({ api, store, ui }) {
  async function loadSettings() {
    try {
      store.set({ settings: await api.settings() });
    } catch {
      // sem a seção de ajustes o resto do painel continua funcionando
    }
  }

  async function saveSettings(body) {
    try {
      store.set({ settings: await api.saveSettings(body) });
      ui.toast(body.reset ? "Padrão restaurado" : "Ajustes salvos", "ok");
    } catch (error) {
      ui.toast(`Não foi possível salvar: ${error.message}`, "bad");
      await loadSettings();
    }
  }

  return {
    loadSettings,
    saveSettings,
    resetSettings: () => saveSettings({ reset: true }),
    openSettings: () => store.set({ settingsOpen: true }),
    closeSettings: () => store.set({ settingsOpen: false }),
  };
}
