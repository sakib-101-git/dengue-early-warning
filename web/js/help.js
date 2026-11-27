// Header "?" popover: one line per tab. Opens on click, closes on Esc, outside click or picking a tab.
export function mountHelp(root) {
  if (!root) return;
  const btn = root.querySelector(".help-btn");
  const pop = root.querySelector(".help-pop");
  const outside = (e) => { if (!root.contains(e.target)) close(false); };
  const open = () => {
    pop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", outside, true);
  };
  function close(focus = true) {
    if (pop.hidden) return;
    pop.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
    if (focus) btn.focus();
  }
  btn.addEventListener("click", () => (pop.hidden ? open() : close()));
  root.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) { e.preventDefault(); close(); } });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) close(false); });
  addEventListener("popstate", () => close(false));
  return { close };
}
