import { BITS, makeCode } from "./words.js";

/**
 * The pairing code demo. Each word is its own span so the entrance can
 * stagger them, and so a long code wraps instead of pushing the page wide.
 */
export function initCodeGen({ gsap, reduced }) {
  const out = document.querySelector("#code-words");
  const button = document.querySelector("#roll");
  const odds = document.querySelector("#odds");
  if (!out || !button) return;

  odds.textContent =
    `about ${BITS.toFixed(0)} bits of entropy, and six wrong guesses blocks that address`;

  function paint(words) {
    out.textContent = "";
    words.forEach((word, i) => {
      if (i) {
        const sep = document.createElement("span");
        sep.className = "sep";
        sep.textContent = "-";
        out.append(sep);
      }
      const span = document.createElement("span");
      span.className = "word";
      span.textContent = word;
      out.append(span);
    });
    return [...out.querySelectorAll(".word")];
  }

  paint(["scout", "gopher", "crater", "nest"]);

  button.addEventListener("click", () => {
    const spans = paint(makeCode());
    if (reduced || !gsap) return;
    gsap.from(spans, {
      opacity: 0,
      y: 7,
      duration: 0.34,
      ease: "power2.out",
      stagger: 0.05,
    });
  });
}
