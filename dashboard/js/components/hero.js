import { h } from "../core/dom.js";
import { watch } from "../core/store.js";
import { TestCard } from "./test-card.js";
import { HeroSlate } from "./hero-slate.js";
import { HeroStaged } from "./hero-staged.js";
import { HeroLive } from "./hero-live.js";

/**
 * Modos do herói: "live" (processando), "staged" (arquivos escolhidos ou enviando),
 * "waiting" (fila esperando para iniciar) e "empty" (nada a fazer: pronto para receber aulas).
 */
export function heroMode({ status, staging, upload }) {
  if (status?.running) return "live";
  if (upload || staging.count) return "staged";
  if (status && status.queue.length + (status.current ? 1 : 0) > 0) return "waiting";
  return "empty";
}

/** O painel principal: uma só tela que muda conforme o que o usuário precisa fazer agora. */
export function Hero(context) {
  const card = TestCard();
  const slate = HeroSlate(context);
  const staged = HeroStaged(context);
  const live = HeroLive(context);
  const el = h("section", { class: "panel hero", "aria-label": "Situação atual" }, card.el, h("div", { class: "hero__body" }, slate.el, staged.el, live.el));

  const paintMode = watch(
    s => [heroMode(s)],
    ([mode]) => {
      el.dataset.mode = mode;
      slate.el.hidden = mode !== "empty" && mode !== "waiting";
      staged.el.hidden = mode !== "staged";
      live.el.hidden = mode !== "live";
    },
  );

  return {
    el,
    update(state) {
      paintMode(state);
      slate.update(state);
      staged.update(state);
      live.update(state);
    },
  };
}
