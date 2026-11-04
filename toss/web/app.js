/* toss receiver. Joins the hub with a code, then sends and receives sealed
   items. Everything is encrypted here in the page, so the hub only ever moves
   ciphertext around. */

const ITERATIONS = 200000;
const CHUNK = 192 * 1024;      // under the hub's message cap once base64'd
const enc = new TextEncoder();
const dec = new TextDecoder();

const $ = (s) => document.querySelector(s);
const state = { ws: null, key: null, code: null, name: null, me: null, peers: [],
                pending: new Map(), retry: 0, giveUp: false };

const store = {
  get(k) { try { return localStorage.getItem("toss-" + k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem("toss-" + k, v); } catch {} },
  drop(k) { try { localStorage.removeItem("toss-" + k); } catch {} },
};

/* ---------- crypto, matching toss/crypto.py ---------- */

async function deriveKey(code, saltB64, iterations) {
  const normalized = String(code).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").join("-");
  const base = await crypto.subtle.importKey("raw", enc.encode(normalized), "PBKDF2", false, ["deriveKey", "deriveBits"]);
  const salt = b64ToBytes(saltB64);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations: iterations || ITERATIONS, hash: "SHA-256" }, base, 256);
  return {
    raw: new Uint8Array(bits),
    aes: await crypto.subtle.importKey("raw", bits, "AES-GCM", false, ["encrypt", "decrypt"]),
    hmac: await crypto.subtle.importKey("raw", bits, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]),
  };
}

async function proof(key, challengeB64) {
  const sig = await crypto.subtle.sign("HMAC", key.hmac, b64ToBytes(challengeB64));
  return bytesToB64(new Uint8Array(sig));
}

async function fingerprint(key) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256",
    concat(enc.encode("toss-fingerprint"), key.raw)));
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  return [...digest.slice(0, 6)].map((b) => alphabet[b % alphabet.length]).join("");
}

async function seal(key, bytes) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key.aes, bytes);
  return { iv: bytesToB64(iv), ct: bytesToB64(new Uint8Array(ct)) };
}

async function unseal(key, env) {
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: b64ToBytes(env.iv) }, key.aes, b64ToBytes(env.ct));
  return new Uint8Array(plain);
}

function bytesToB64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
function b64ToBytes(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a); out.set(b, a.length);
  return out;
}

/* ---------- connection ---------- */

function connect(code, name) {
  return new Promise((resolve, reject) => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/`);
    let settled = false;

    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.t === "hello") {
        try {
          state.key = await deriveKey(code, msg.salt, msg.iterations);
          ws.send(JSON.stringify({ t: "auth", name, proof: await proof(state.key, msg.challenge) }));
        } catch (err) { reject(err); }
      } else if (msg.t === "ready") {
        state.me = msg.you;
        state.peers = msg.peers;
        settled = true;
        resolve({ fingerprint: await fingerprint(state.key), hub: msg.fingerprint, peers: msg.peers });
      } else {
        handle(msg);
      }
    };

    ws.onclose = (ev) => {
      if (!settled) {
        reject(new Error(ev.code === 4001 ? "That code does not match."
          : ev.code === 4029 ? "Too many wrong codes from here. Wait a minute."
          : "Could not join. Is the hub still running?"));
      } else {
        onDisconnected();
      }
    };
    ws.onerror = () => { if (!settled) reject(new Error("Could not reach the hub on this network.")); };
    state.ws = ws;
  });
}

async function handle(msg) {
  if (msg.t === "joined") { state.peers.push(msg.peer); renderPeers(); toast(`${msg.peer.name} joined`); }
  else if (msg.t === "left") { state.peers = state.peers.filter((p) => p.id !== msg.peer.id); renderPeers(); }
  else if (msg.t === "item") {
    const payload = JSON.parse(dec.decode(await unseal(state.key, msg)));
    addItem({ ...payload, from: msg.from && msg.from.name });
  } else if (msg.t === "chunk") {
    const part = await unseal(state.key, msg);
    let entry = state.pending.get(msg.id);
    if (!entry) { entry = { parts: new Array(msg.n), got: 0 }; state.pending.set(msg.id, entry); }
    entry.parts[msg.i] = part;
    entry.got++;
    progress(msg.id, entry.got, msg.n, msg.name);
    if (entry.got === msg.n) {
      state.pending.delete(msg.id);
      const total = entry.parts.reduce((n, p) => n + p.length, 0);
      const joined = new Uint8Array(total);
      let at = 0;
      for (const p of entry.parts) { joined.set(p, at); at += p.length; }
      addItem({ kind: "file", name: msg.name, mime: msg.mime, bytes: joined, from: msg.from && msg.from.name });
    }
  }
}

/* ---------- sending ---------- */

async function sendText(text) {
  const body = enc.encode(JSON.stringify({ kind: "text", data: text, at: Date.now() }));
  state.ws.send(JSON.stringify({ t: "item", ...(await seal(state.key, body)) }));
  addItem({ kind: "text", data: text, mine: true });
}

async function sendFile(file) {
  const id = bytesToB64(crypto.getRandomValues(new Uint8Array(9)));
  const buf = new Uint8Array(await file.arrayBuffer());
  const n = Math.max(1, Math.ceil(buf.length / CHUNK));
  for (let i = 0; i < n; i++) {
    const env = await seal(state.key, buf.subarray(i * CHUNK, (i + 1) * CHUNK));
    state.ws.send(JSON.stringify({
      t: "chunk", id, i, n, name: file.name, mime: file.type || "application/octet-stream", ...env,
    }));
    progress(id, i + 1, n, file.name);
  }
  addItem({ kind: "file", name: file.name, mime: file.type, bytes: buf, mine: true });
}

/* ---------- ui ---------- */

const items = [];

function addItem(item) {
  items.unshift(item);
  renderItems();
  if (!item.mine) {
    toast(item.kind === "file" ? `Got ${item.name}` : "Got something");
    if (navigator.vibrate) navigator.vibrate(60);
  }
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const isUrl = (s) => /^https?:\/\/\S+$/i.test(s.trim());
const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;

function renderItems() {
  $("#items").innerHTML = items.map((it, i) => {
    const who = it.mine ? "you" : (it.from || "them");
    if (it.kind === "file") {
      return `<li class="${it.mine ? "mine" : ""}">
        <div class="meta">${esc(who)} &middot; file</div>
        <div class="body"><strong>${esc(it.name)}</strong> <span class="dim">${size(it.bytes.length)}</span></div>
        <div class="acts"><button data-save="${i}">Save</button></div></li>`;
    }
    const body = isUrl(it.data)
      ? `<a href="${esc(it.data)}" target="_blank" rel="noopener noreferrer">${esc(it.data)}</a>`
      : `<span class="text">${esc(it.data)}</span>`;
    return `<li class="${it.mine ? "mine" : ""}">
      <div class="meta">${esc(who)} &middot; ${isUrl(it.data) ? "link" : "text"}</div>
      <div class="body">${body}</div>
      <div class="acts"><button data-copy="${i}">Copy</button>${
        isUrl(it.data) ? `<button data-open="${i}">Open</button>` : ""}</div></li>`;
  }).join("");
  $("#empty-list").hidden = items.length > 0;
}

function renderPeers() {
  const n = state.peers.length;
  $("#peers").textContent = n === 0 ? "nothing else connected yet"
    : n === 1 ? `${state.peers[0].name} connected`
    : `${n} devices connected`;
}

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

function progress(id, done, total, name) {
  if (total <= 1) return;
  toast(`${name}: ${Math.round((done / total) * 100)}%`);
}

/* The page lives on a phone that sleeps, so dropping the socket is normal and
   getting back on quietly is the whole convenience. */
async function reconnect() {
  if (state.giveUp) return;
  state.retry++;
  const wait = Math.min(1000 * 2 ** (state.retry - 1), 15000);
  $("#peers").textContent = `reconnecting in ${Math.round(wait / 1000)}s...`;
  await new Promise((r) => setTimeout(r, wait));
  if (state.giveUp) return;
  try {
    const info = await connect(state.code, state.name);
    state.retry = 0;
    $("#dot").classList.remove("off");
    $("#fp").textContent = info.fingerprint;
    renderPeers();
    toast("Back on");
  } catch (err) {
    if (/code/i.test(err.message)) {
      // the laptop rotated its code, so stop guessing and ask
      state.giveUp = true;
      forget("That code no longer works. Enter the new one.");
      return;
    }
    reconnect();
  }
}

function onDisconnected() {
  $("#dot").classList.add("off");
  $("#peers").textContent = "disconnected";
  reconnect();
}

function forget(message) {
  store.drop("code");
  state.giveUp = true;
  try { state.ws && state.ws.close(); } catch {}
  $("#room").hidden = true;
  $("#gate").hidden = false;
  $("#join-error").textContent = message || "";
  $("#code").value = "";
}

function save(item) {
  const url = URL.createObjectURL(new Blob([item.bytes], { type: item.mime || "application/octet-stream" }));
  const a = document.createElement("a");
  a.href = url; a.download = item.name; a.click();
  URL.revokeObjectURL(url);
}

/* ---------- wiring ---------- */

function init() {
  const params = new URLSearchParams(location.hash.slice(1));
  const fromLink = params.get("code");
  const saved = store.get("code");
  if (fromLink) $("#code").value = fromLink;
  else if (saved) $("#code").value = saved;
  $("#name").value = store.get("name") || guessName();

  $("#join").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("#code").value.trim();
    const name = $("#name").value.trim() || "a device";
    if (!code) return;
    $("#join-error").textContent = "";
    $("#join button").disabled = true;
    $("#join button").textContent = "Joining...";
    try {
      const info = await connect(code, name);
      state.code = code;
      state.name = name;
      state.giveUp = false;
      state.retry = 0;
      store.set("name", name);
      store.set("code", code);   // so you never type it twice
      $("#gate").hidden = true;
      $("#room").hidden = false;
      $("#fp").textContent = info.fingerprint;
      renderPeers();
      renderItems();
      // keep phones from dozing the socket off
      setInterval(() => { try { state.ws.send(JSON.stringify({ t: "ping" })); } catch {} }, 15000);
    } catch (err) {
      $("#join-error").textContent = err.message;
    } finally {
      $("#join button").disabled = false;
      $("#join button").textContent = "Join";
    }
  });

  $("#forget").addEventListener("click", () => forget("Paired device forgotten."));

  // Coming back to the tab after the phone slept should just work.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    const dead = !state.ws || state.ws.readyState > 1;
    if (dead && state.code && !state.giveUp) { state.retry = 0; reconnect(); }
  });
  window.addEventListener("online", () => {
    if (state.code && !state.giveUp && (!state.ws || state.ws.readyState > 1)) {
      state.retry = 0;
      reconnect();
    }
  });

  // Paste straight into the page, no typing in the box first.
  document.addEventListener("paste", async (e) => {
    if ($("#room").hidden) return;
    if (/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    const files = [...(e.clipboardData.files || [])];
    if (files.length) { e.preventDefault(); for (const f of files) await sendFile(f); return; }
    const text = e.clipboardData.getData("text");
    if (text && text.trim()) { e.preventDefault(); await sendText(text); }
  });

  // Enter sends, newline needs shift, like every chat box people already know.
  $("#compose").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#send").requestSubmit();
    }
  });

  $("#send").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#compose").value;
    if (!text.trim()) return;
    await sendText(text);
    $("#compose").value = "";
  });

  $("#file").addEventListener("change", async (e) => {
    for (const f of e.target.files) await sendFile(f);
    e.target.value = "";
  });
  $("#attach").addEventListener("click", () => $("#file").click());

  document.addEventListener("dragover", (e) => { e.preventDefault(); $("#room").classList.add("drag"); });
  document.addEventListener("dragleave", () => $("#room").classList.remove("drag"));
  document.addEventListener("drop", async (e) => {
    e.preventDefault();
    $("#room").classList.remove("drag");
    for (const f of e.dataTransfer.files) await sendFile(f);
  });

  $("#items").addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    if (btn.dataset.copy !== undefined) {
      const it = items[+btn.dataset.copy];
      try { await navigator.clipboard.writeText(it.data); toast("Copied"); }
      catch { toast("Could not reach the clipboard"); }
    } else if (btn.dataset.save !== undefined) {
      save(items[+btn.dataset.save]);
    } else if (btn.dataset.open !== undefined) {
      window.open(items[+btn.dataset.open].data, "_blank", "noopener");
    }
  });
}

/** A device that has paired before should not be asked anything at all.
    A fresh QR link still gets to name itself, which is a one-time thing. */
async function autoJoin() {
  const code = store.get("code");
  if (!code) return;
  $("#join-error").textContent = "";
  $("#join button").textContent = "Reconnecting...";
  $("#join button").disabled = true;
  try {
    $("#code").value = code;
    $("#join").requestSubmit();
  } catch {
    $("#join button").disabled = false;
    $("#join button").textContent = "Join";
  }
}

function guessName() {
  const ua = navigator.userAgent;
  if (/iPhone/i.test(ua)) return "iPhone";
  if (/iPad/i.test(ua)) return "iPad";
  if (/Android/i.test(ua)) return "Android phone";
  if (/Mac/i.test(ua)) return "Mac";
  if (/Windows/i.test(ua)) return "Windows laptop";
  if (/Linux/i.test(ua)) return "Linux box";
  return "a device";
}

function boot() {
  init();
  autoJoin();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
