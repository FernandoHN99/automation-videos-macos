/** Estado único do painel: quem muda chama `set`, quem desenha assina com `subscribe`. */
export function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...patch };
      listeners.forEach(listener => listener(state));
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

const UNSET = Symbol("unset");

/**
 * Desenha só quando a fatia do estado mudou. `select` devolve um array de dependências
 * (comparadas por identidade); `render` recebe esse array e o estado completo.
 */
export function watch(select, render) {
  let previous = UNSET;
  return state => {
    const next = select(state);
    if (previous !== UNSET && next.every((value, i) => Object.is(value, previous[i]))) return;
    previous = next;
    render(next, state);
  };
}
