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

  function deliver(selected) {
    if (flying) return;
    flying = true;
    setHint(selected ? "<b>your selection is on the phone.</b> that is the whole gesture"
                     : "<b>sent.</b> that is the whole gesture");

    const item = document.createElement("div");
    item.className = "demo-item";
    item.textContent = selected ? (selected.length > 90 ? selected.slice(0, 87) + "..." : selected) : SAMPLE;
    if (gsap && !reduced) burst(gsap, key, phone, path, chip);
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
    const selected = String(window.getSelection() || "").trim();
    if (gsap && !reduced) charge(gsap, key, HOLD_MS);
    timer = setTimeout(() => {
      holding = false;
      key.dataset.held = "false";
      deliver(selected);
    }, HOLD_MS);
  }

  function stop() {
    if (!holding) return;
    holding = false;
    key.dataset.held = "false";
    clearTimeout(timer);
    ring(0, 180);
    uncharge(gsap);
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
          if (!played) setHint("now you: highlight any text here, then hold <b>right ctrl</b>");
        }, 900);
      }, HOLD_MS);
    }, 1100);
  }
}


/* ---------- the effect ----------
   Holding charges the key: the page dims around the demo and sparks are pulled
   into the keycap. At 0.4s it fires: a shockwave leaves the key, the clip
   streaks along the wire with a trail, and the phone lights up. */

let layer, veil, chargeTl;

function fxLayer() {
  if (layer) return layer;
  layer = document.createElement("div");
  layer.className = "fx-layer";
  layer.setAttribute("aria-hidden", "true");
  veil = document.createElement("div");
  veil.className = "fx-veil";
  document.body.append(veil, layer);
  return layer;
}

function center(el) {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, r };
}

function dot(cls, x, y) {
  const d = document.createElement("span");
  d.className = cls;
  d.style.left = x + "px";
  d.style.top = y + "px";
  fxLayer().append(d);
  return d;
}

function charge(gsap, key, ms) {
  fxLayer();
  const c = center(key);
  veil.style.setProperty("--fx-x", c.x + "px");
  veil.style.setProperty("--fx-y", c.y + "px");
  document.querySelector("#demo")?.classList.add("is-charging");
  chargeTl = gsap.timeline();
  chargeTl.to(veil, { opacity: 1, duration: ms / 1000, ease: "power1.in" }, 0);
  chargeTl.to(key, { scale: 1.06, duration: ms / 1000, ease: "power2.in" }, 0);
  for (let i = 0; i < 14; i++) {
    const a = Math.random() * Math.PI * 2;
    const dist = 70 + Math.random() * 90;
    const s = dot("fx-spark", c.x + Math.cos(a) * dist, c.y + Math.sin(a) * dist);
    chargeTl.fromTo(s, { opacity: 0, scale: 0.4 },
      { x: -Math.cos(a) * dist, y: -Math.sin(a) * dist, opacity: 1, scale: 1,
        duration: ms / 1000 * (0.6 + Math.random() * 0.4), ease: "power2.in",
        onComplete: () => s.remove() }, Math.random() * 0.12);
  }
}

function uncharge(gsap) {
  document.querySelector("#demo")?.classList.remove("is-charging");
  if (chargeTl) chargeTl.kill();
  if (!gsap || !layer) return;
  layer.querySelectorAll(".fx-spark").forEach((s) => s.remove());
  gsap.to(veil, { opacity: 0, duration: 0.25 });
  gsap.to("#keycap", { scale: 1, duration: 0.25 });
}

function burst(gsap, key, phone, path, chip) {
  fxLayer();
  document.querySelector("#demo")?.classList.remove("is-charging");
  const k = center(key);
  const p = center(phone);
  const tl = gsap.timeline();

  tl.to(key, { scale: 0.94, duration: 0.08, ease: "power2.out" }, 0)
    .to(key, { scale: 1, duration: 0.5, ease: "elastic.out(1, 0.4)" }, 0.08);

  // shockwaves out of the key
  for (let i = 0; i < 3; i++) {
    const w = dot("fx-wave", k.x, k.y);
    tl.fromTo(w, { scale: 0.2, opacity: 0.85 },
      { scale: 9 + i * 4, opacity: 0, duration: 1.0 + i * 0.25, ease: "expo.out",
        onComplete: () => w.remove() }, i * 0.07);
  }

  // sparks flung outward
  for (let i = 0; i < 22; i++) {
    const a = Math.random() * Math.PI * 2;
    const d = 60 + Math.random() * 140;
    const s = dot("fx-spark", k.x, k.y);
    tl.fromTo(s, { opacity: 1, scale: 1 },
      { x: Math.cos(a) * d, y: Math.sin(a) * d, opacity: 0, scale: 0.2,
        duration: 0.6 + Math.random() * 0.5, ease: "expo.out", onComplete: () => s.remove() }, 0.02);
  }

  // a trail of ghosts following the chip along the wire
  const wire = path.closest(".demo-wire");
  for (let i = 1; i <= 5; i++) {
    const g = chip.cloneNode();
    g.removeAttribute("id");
    g.classList.add("fx-ghost");
    wire.append(g);
    tl.set(g, { opacity: 0.55 - i * 0.09, scale: 0.9 - i * 0.1 }, 0)
      .to(g, { motionPath: { path, align: path, alignOrigin: [0.5, 0.5] },
               duration: 0.52, ease: "power2.inOut" }, i * 0.035)
      .to(g, { opacity: 0, duration: 0.12, onComplete: () => g.remove() }, 0.46 + i * 0.035);
  }

  // the phone catches it
  phone.classList.remove("is-hit");
  void phone.offsetWidth;
  tl.add(() => phone.classList.add("is-hit"), 0.5);
  const ring = dot("fx-wave fx-wave-phone", p.x, p.y);
  tl.fromTo(ring, { scale: 0.4, opacity: 0 },
    { scale: 4.5, opacity: 0, keyframes: { opacity: [0, 0.8, 0] }, duration: 0.8, ease: "expo.out",
      onComplete: () => ring.remove() }, 0.5);
  for (let i = 0; i < 12; i++) {
    const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.4;
    const d = 40 + Math.random() * 60;
    const s = dot("fx-spark", p.x, p.r.top + 20);
    tl.fromTo(s, { opacity: 1 }, { x: Math.cos(a) * d, y: Math.sin(a) * d, opacity: 0,
      duration: 0.7, ease: "expo.out", onComplete: () => s.remove() }, 0.52);
  }

  tl.to(veil, { opacity: 0, duration: 0.7, ease: "power2.out" }, 0.55);
  tl.add(() => phone.classList.remove("is-hit"), 1.6);
}
