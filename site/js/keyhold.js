/**
 * The hold-to-send demo: the one thing on this page that shows rather than
 * tells. Press and hold the keycap and a ring fills over the same 0.4s the
 * real hotkey waits. Let go early and it rewinds, exactly like the tool.
 */
const HOLD_MS = 400;
const SAMPLE = "https://news.ycombinator.com/item?id=1";

export function initKeyHold({ gsap, reduced }) {
  const key = document.querySelector("#keycap");
  const fill = document.querySelector("#ring-fill");
  const chip = document.querySelector("#chip");
  const phone = document.querySelector("#demo-phone");
  const hint = document.querySelector("#demo-hint");
  const path = document.querySelector("#wire-path");
  if (!key || !fill || !chip || !phone || !path) return;

  const len = fill.getTotalLength ? fill.getTotalLength() : 2 * Math.PI * 52;
  fill.style.strokeDasharray = String(len);
  fill.style.strokeDashoffset = String(len);

  let holding = false;
  let timer = null;
  let flying = false;
  let played = false;   // set once the visitor takes over, so autoplay backs off

  const setHint = (text) => { if (hint) hint.innerHTML = text; };

  function ring(to, ms) {
    if (!gsap || reduced) {
      fill.style.strokeDashoffset = String(len * (1 - to));
      fill.style.opacity = to > 0 ? "1" : "0";
      return;
    }
    gsap.to(fill, {
      strokeDashoffset: len * (1 - to),
      opacity: to > 0 ? 1 : 0,
      duration: ms / 1000,
      ease: to > 0 ? "none" : "power2.out",
      overwrite: true,
    });
  }

  function deliver() {
    if (flying) return;
    flying = true;
    setHint("<b>sent.</b> that is the whole gesture");

    const item = document.createElement("div");
    item.className = "demo-item";
    item.textContent = SAMPLE;
    phone.append(item);
    while (phone.querySelectorAll(".demo-item").length > 3) {
      phone.querySelector(".demo-item").remove();
    }

    const done = () => {
      flying = false;
      ring(0, 220);
    };

    if (!gsap || reduced) {
      item.style.opacity = "1";
      done();
      return;
    }

    gsap.timeline({ onComplete: done })
      .set(chip, { opacity: 1, scale: 0.6 })
      .to(chip, {
        motionPath: { path, align: path, alignOrigin: [0.5, 0.5] },
        scale: 1,
        duration: 0.52,
        ease: "power2.inOut",
      }, 0)
      .to(chip, { opacity: 0, scale: 0.5, duration: 0.16, ease: "power2.in" }, 0.46)
      .from(item, { opacity: 0, y: 8, scale: 0.9, duration: 0.3, ease: "back.out(2)" }, 0.5)
      .to(phone, { scale: 1.045, duration: 0.14, yoyo: true, repeat: 1, ease: "power2.out" }, 0.48);
  }

  function start(event) {
    if (event.type === "keydown" && event.repeat) return;
    if (holding || flying) return;
    event.preventDefault();
    played = true;
    holding = true;
    key.dataset.held = "true";
    setHint("keep holding...");
    ring(1, HOLD_MS);
    timer = setTimeout(() => {
      holding = false;
      key.dataset.held = "false";
      deliver();
    }, HOLD_MS);
  }

  function stop() {
    if (!holding) return;
    holding = false;
    key.dataset.held = "false";
    clearTimeout(timer);
    ring(0, 180);
    setHint("too quick. hold it for <b>0.4 seconds</b>");
  }

  /* The real key works too: hold right-ctrl anywhere on the page and the demo
     does exactly what the tool does on your laptop. */
  window.addEventListener("keydown", (e) => {
    if (e.code !== "ControlRight") return;
    e.preventDefault();
    played = true;
    start(e);
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "ControlRight") stop();
  });

  key.addEventListener("pointerdown", start);
  key.addEventListener("pointerup", stop);
  key.addEventListener("pointercancel", stop);
  key.addEventListener("pointerleave", stop);
  key.addEventListener("keydown", (e) => {
    if (e.key === " " || e.key === "Enter") start(e);
  });
  key.addEventListener("keyup", (e) => {
    if (e.key === " " || e.key === "Enter") stop();
  });
  // a stray release anywhere should not leave the ring stuck
  window.addEventListener("pointerup", stop);
  window.addEventListener("blur", stop);

  /* Play it through once on arrival. Showing the gesture beats describing it,
     and it stops the moment you touch anything yourself. */
  if (!reduced) {
    setTimeout(() => {
      if (played || holding || flying) return;
      key.dataset.held = "true";
      setHint("holding...");
      ring(1, HOLD_MS);
      setTimeout(() => {
        key.dataset.held = "false";
        if (played || holding) return;
        deliver();
        setTimeout(() => {
          if (!played) setHint("now you: hold <b>right ctrl</b>, or the key above");
        }, 900);
      }, HOLD_MS);
    }, 1100);
  }
}
