import { h } from "./core/dom.js";
import { api } from "./core/api.js";
import { createStore } from "./core/store.js";
import { Staging, EMPTY_STAGING } from "./features/staging.js";
import { createRunController, startPolling } from "./features/run-controller.js";
import { createStagingController, LOCKED_MESSAGE } from "./features/staging-controller.js";
import { createSettingsController } from "./features/settings-controller.js";
import { createFilePicker } from "./features/file-picker.js";
import { watchFileDrops } from "./features/file-drops.js";
import { Toasts } from "./components/toasts.js";
import { ConfirmDialog } from "./components/confirm-dialog.js";
import { Topbar } from "./components/topbar.js";
import { Hero } from "./components/hero.js";
import { SavingsPanel } from "./components/savings-panel.js";
import { DiskPanel } from "./components/disk-panel.js";
import { Timeline } from "./components/timeline.js";
import { QueuePanel } from "./components/queue-panel.js";
import { RecentPanel } from "./components/recent-panel.js";
import { RecipePanel } from "./components/recipe-panel.js";
import { SettingsDrawer } from "./components/settings-drawer.js";
import { DropOverlay } from "./components/drop-overlay.js";

/* Raiz de composição: cria o estado, liga os controladores e monta os componentes. */

const store = createStore({
  status: null, // último /api/status
  offline: false,
  settings: null, // último /api/settings
  staging: EMPTY_STAGING, // arquivos escolhidos, ainda não enviados
  upload: null, // progresso do envio em andamento
  dragging: false, // arrastando arquivos sobre a janela
  queueTab: "queue",
  settingsOpen: false,
});

const toasts = Toasts();
const confirmDialog = ConfirmDialog();
const ui = { toast: toasts.toast, confirm: confirmDialog.confirm };

const run = createRunController({ api, store, ui });
const staging = createStagingController({ api, store, staging: new Staging(), ui, startRun: run.startRun });
const settings = createSettingsController({ api, store, ui });
const actions = { ...run, ...staging, ...settings };
const context = { store, actions, ui, pickers: createFilePicker(actions.addFiles) };

const topbar = Topbar(context);
const hero = Hero(context);
const savings = SavingsPanel(context);
const disk = DiskPanel(context);
const timeline = Timeline(context);
const queue = QueuePanel(context);
const recent = RecentPanel(context);
const recipe = RecipePanel(context);
const drawer = SettingsDrawer(context);
const dropOverlay = DropOverlay(context);

const parts = [topbar, hero, savings, disk, timeline, queue, recent, recipe, drawer, dropOverlay];

document.getElementById("app").replaceChildren(
  h("main", { class: "console" }, topbar.el, hero.el, h("div", { class: "side" }, savings.el, disk.el), timeline.el, queue.el, recent.el, recipe.el),
  drawer.el,
  dropOverlay.el,
  toasts.el,
  confirmDialog.el,
);

const render = state => parts.forEach(part => part.update(state));
store.subscribe(render);
render(store.get());

watchFileDrops({
  isLocked: () => Boolean(store.get().status?.running),
  onDrop: actions.addDropped,
  onBlocked: () => ui.toast(LOCKED_MESSAGE, "bad"),
  onDragging: dragging => store.set({ dragging }),
});

addEventListener("beforeunload", event => {
  if (store.get().upload) {
    event.preventDefault();
    event.returnValue = "";
  }
});

startPolling(actions.refresh);
actions.loadSettings();
