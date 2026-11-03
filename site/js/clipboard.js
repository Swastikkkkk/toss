/** Copy buttons on the terminal blocks. */
export function initCopy() {
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const block = button.closest(".term");
      const text = block ? block.querySelector("pre").innerText : "";
      const original = button.textContent;
      try {
        await navigator.clipboard.writeText(text.trim());
        button.textContent = "copied";
      } catch {
        // some embedded views refuse the clipboard; select it instead
        const range = document.createRange();
        range.selectNodeContents(block.querySelector("pre"));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        button.textContent = "selected";
      }
      setTimeout(() => { button.textContent = original; }, 1600);
    });
  });
}
