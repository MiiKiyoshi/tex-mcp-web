// The ⋯ menus, of the top bar and of the editor's bar.

// A ⋯ button and its menu. The button opens and closes the menu; a click outside it, a click
// on an item that `closes` accepts, or Escape closes it. The menu takes Escape before the
// editor and the comment keys do. `opening` readies the items as the menu opens.
export function attachMenu(button, menu, { closes, opening = () => {} }) {
  const close = () => {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };
  button.addEventListener("click", () => {
    const open = menu.hidden;
    if (open) opening();
    menu.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (event) => {
    if (!button.parentElement.contains(event.target) || closes(event.target)) close();
  }, { capture: true });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || menu.hidden) return;
    event.preventDefault();
    event.stopPropagation();
    close();
  }, { capture: true });
}
