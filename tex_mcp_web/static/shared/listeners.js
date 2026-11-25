// The functions a part calls when a fact it owns changes, which other parts hand it: the part
// tells what happened, and each listener decides what to do about it.
//
// createListeners takes nothing. It gives add(listener), which hands a function in, and
// tell(...args), which calls each one handed in, in the order they came.

export function createListeners() {
  const listeners = [];
  return {
    add: (listener) => { listeners.push(listener); },
    tell: (...args) => { for (const listener of listeners) listener(...args); },
  };
}
