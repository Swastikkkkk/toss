/**
 * Copy buttons. Each one reads the command sitting next to it, so the text on
 * screen and the text on your clipboard can never drift apart.
 */
export function initCopy() {
  const toast = document.querySelector("#toast");

  function flash(message) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => toast.classList.remove("show"), 1800);
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const block = button.closest(".cmd") || button.closest(".term");
      const source = block && (block.querySelector("code") || block.querySelector("pre"));
      const text = source ? source.textContent.trim() : "";
      if (!text) return;

      const label = button.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
        button.classList.add("done");
        flash("Copied to clipboard");
      } catch {
        // some embedded views refuse the clipboard, so select it instead
        const range = document.createRange();
        range.selectNodeContents(source);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        button.textContent = "Selected";
        flash("Selected. Press Ctrl+C");
      }
      setTimeout(() => {
        button.textContent = label;
        button.classList.remove("done");
      }, 1800);
    });
  });
}
