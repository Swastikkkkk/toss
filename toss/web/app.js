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

import { gcm, pbkdf2Async, hmac, sha256 } from "./vendor-crypto.js";

/* ---------- crypto, matching toss/crypto.py ----------
   Browsers only give WebCrypto to https pages. toss runs on plain http on
   your wifi so there is no certificate warning, so the audited noble library
   does the work instead. PBKDF2 uses the native one when it exists, it is
   faster. The derived key is cached so reopening the page is instant. */

const native = !!(window.isSecureContext && window.crypto && crypto.subtle);

async function deriveKey(code, saltB64, iterations) {
  const normalized = String(code).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").join("-");
  const iters = iterations || ITERATIONS;
  const cacheId = "key-" + bytesToB64(sha256(enc.encode(`${normalized}|${saltB64}|${iters}`))).slice(0, 16);
  const cached = store.get(cacheId);
  if (cached) return { raw: b64ToBytes(cached) };
  const salt = b64ToBytes(saltB64);
  let raw;
  if (native) {
    const base = await crypto.subtle.importKey("raw", enc.encode(normalized), "PBKDF2", false, ["deriveBits"]);
    raw = new Uint8Array(await crypto.subtle.deriveBits(
      { name: "PBKDF2", salt, iterations: iters, hash: "SHA-256" }, base, 256));
  } else {
    raw = await pbkdf2Async(sha256, enc.encode(normalized), salt, { c: iters, dkLen: 32 });
  }
  store.set(cacheId, bytesToB64(raw));
  return { raw };
}

async function proof(key, challengeB64) {
  return bytesToB64(hmac(sha256, key.raw, b64ToBytes(challengeB64)));
}

async function fingerprint(key) {
  const digest = sha256(concat(enc.encode("toss-fingerprint"), key.raw));
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  return [...digest.slice(0, 6)].map((b) => alphabet[b % alphabet.length]).join("");
}

async function seal(key, bytes) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  return { iv: bytesToB64(iv), ct: bytesToB64(gcm(key.raw, iv).encrypt(bytes)) };
}

async function unseal(key, env) {
  return gcm(key.raw, b64ToBytes(env.iv)).decrypt(b64ToBytes(env.ct));
}

/* Copy text without the Clipboard API, which http pages do not get.
   Works inside a tap on iPhone and Android. */
function legacyCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;font-size:16px";
  document.body.appendChild(ta);
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  return ok;
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try { await navigator.clipboard.writeText(text); return true; } catch {}
  }
  return legacyCopy(text);
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
          const had = store.get("epoch");
          const seen = had === null ? null : had === msg.epoch ? Number(store.get("seq") || 0) : 0;
          if (store.get("epoch") !== msg.epoch) { store.set("epoch", msg.epoch || ""); store.set("seq", "0"); }
          ws.send(JSON.stringify({ t: "auth", name, proof: await proof(state.key, msg.challenge),
                                   since: seen, epoch: msg.epoch }));
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
  if (msg.seq && msg.epoch === store.get("epoch") && msg.seq > Number(store.get("seq") || 0)) {
    if (msg.t === "item" || (msg.t === "chunk" && msg.i === msg.n - 1)) store.set("seq", String(msg.seq));
  }
  if (msg.t === "joined") { state.peers.push(msg.peer); renderPeers(); toast(`${msg.peer.name} joined`); }
  else if (msg.t === "left") { state.peers = state.peers.filter((p) => p.id !== msg.peer.id); renderPeers(); }
  else if (msg.t === "item") {
    const payload = JSON.parse(dec.decode(await unseal(state.key, msg)));
    const from = msg.from && msg.from.name;
    if (payload.sync) onSync(payload.data, from);
    else addItem({ ...payload, from });
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
      const from = msg.from && msg.from.name;
      if (msg.sync && /^image\//.test(msg.mime || "")) onSyncImage(joined, msg.mime, from);
      else if (msg.sync) onSyncFile(joined, msg.name, msg.mime, from);
      else addItem({ kind: "file", name: msg.name, mime: msg.mime, bytes: joined, from });
    }
  }
}

/* ---------- sending ---------- */

async function sendText(text) {
  const body = enc.encode(JSON.stringify({ kind: "text", data: text, at: Date.now() }));
  state.ws.send(JSON.stringify({ t: "item", ...(await seal(state.key, body)) }));
  addItem({ kind: "text", data: text, mine: true });
}

async function sendFile(file, sync = false) {
  const id = bytesToB64(crypto.getRandomValues(new Uint8Array(9)));
  const buf = new Uint8Array(await file.arrayBuffer());
  const n = Math.max(1, Math.ceil(buf.length / CHUNK));
  for (let i = 0; i < n; i++) {
    const env = await seal(state.key, buf.subarray(i * CHUNK, (i + 1) * CHUNK));
    state.ws.send(JSON.stringify({
      t: "chunk", id, i, n, name: file.name, mime: file.type || "application/octet-stream", sync, ...env,
    }));
    progress(id, i + 1, n, file.name);
  }
  addItem({ kind: "file", name: file.name, mime: file.type, bytes: buf, mine: true });
}

/* ---------- ui ---------- */

const items = [];

/* A copy on the laptop lands here. Chrome and Android let a focused page write
   the clipboard on its own, so try that first. Safari on iPhone refuses any
   clipboard write that is not inside a tap, so there the latest clip stays
   pinned at the top behind one big button. */
let latestClip = null;   // { type: "text", text } or { type: "image", blob, mime }

function showLatest(from) {
  const card = $("#latest");
  card.hidden = false;
  $("#latest-from").textContent = from ? `copied on ${from}` : "copied on your laptop";
  const btn = $("#latest-copy");
  btn.hidden = false;
  btn.textContent = "Tap to copy";
  btn.classList.remove("done");
  const saveBtn = $("#latest-save");
  saveBtn.textContent = "Save image";
  saveBtn.classList.remove("primary");
}

async function autoCopied() {
  const btn = $("#latest-copy");
  btn.textContent = "On your clipboard";
  btn.classList.add("done");
  toast("Copied to this device");
}

async function onSync(text, from) {
  latestClip = { type: "text", text };
  showLatest(from);
  $("#latest-img").hidden = true;
  $("#latest-save").hidden = true;
  const box = $("#latest-text");
  box.hidden = false;
  box.textContent = text.length > 400 ? text.slice(0, 400) + "..." : text;
  if (await tryWrite()) autoCopied();
  else toast("New clip from your laptop");
  if (navigator.vibrate) navigator.vibrate(40);
}

function onSyncImage(bytes, mime, from) {
  const blob = new Blob([bytes], { type: mime || "image/png" });
  latestClip = { type: "image", blob, mime: blob.type };
  showLatest(from);
  $("#latest-text").hidden = true;
  const img = $("#latest-img");
  if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
  img.dataset.url = URL.createObjectURL(blob);
  img.src = img.dataset.url;
  img.hidden = false;
  $("#latest-save").hidden = false;
  tryWrite().then((ok) => {
    if (ok) autoCopied();
    else toast("New image from your laptop");
  });
  if (navigator.vibrate) navigator.vibrate(40);
}

/* A document or any other file copied on the laptop. A phone clipboard cannot
   hold a PDF, so this one is about getting it saved or shared in one tap. */
function onSyncFile(bytes, name, mime, from) {
  const blob = new Blob([bytes], { type: mime || "application/octet-stream" });
  latestClip = { type: "file", blob, mime: blob.type, name };
  showLatest(from);
  $("#latest-img").hidden = true;
  const box = $("#latest-text");
  box.hidden = false;
  box.textContent = `${name}  \u00b7  ${size(bytes.length)}`;
  $("#latest-copy").hidden = true;
  const saveBtn = $("#latest-save");
  saveBtn.hidden = false;
  saveBtn.textContent = "Save or share";
  saveBtn.classList.add("primary");
  addItem({ kind: "file", name, mime: blob.type, bytes, from, quiet: true });
  toast(`${name} from your laptop`);
  if (navigator.vibrate) navigator.vibrate(40);
}

/* Chrome and Android let a focused page write the clipboard on its own.
   Safari only allows it inside a tap, so there this fails and the button waits. */
async function tryWrite() {
  if (!latestClip || !document.hasFocus() || !navigator.clipboard) return false;
  try {
    await writeClip();
    return true;
  } catch {
    return false;
  }
}

function writeClip() {
  if (!navigator.clipboard) return Promise.reject(new Error("no clipboard api"));
  if (latestClip.type === "text") return navigator.clipboard.writeText(latestClip.text);
  if (!window.ClipboardItem || !navigator.clipboard.write) return Promise.reject(new Error("no image clipboard"));
  return navigator.clipboard.write([new ClipboardItem({ [latestClip.mime]: latestClip.blob })]);
}

async function copyLatest() {
  if (!latestClip) return;
  const btn = $("#latest-copy");
  try {
    if (latestClip.type === "text") { if (!(await copyText(latestClip.text))) throw new Error("no"); }
    else await writeClip();
    btn.textContent = "On your clipboard";
    btn.classList.add("done");
    toast("Copied");
  } catch {
    if (latestClip.type === "image") {
      toast("Long-press the image and tap Copy");
      return;
    }
    const range = document.createRange();
    range.selectNodeContents($("#latest-text"));
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    toast("Selected. Tap Copy");
  }
}

/* On iPhone the share sheet is the way to Save Image to Photos. */
async function saveLatest() {
  if (!latestClip || (latestClip.type !== "image" && latestClip.type !== "file")) return;
  const name = latestClip.name || `toss-${Date.now()}.png`;
  const file = new File([latestClip.blob], name, { type: latestClip.mime });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file] }); return; } catch (e) { if (e && e.name === "AbortError") return; }
  }
  const url = URL.createObjectURL(file);
  const a = document.createElement("a");
  a.href = url; a.download = file.name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* The other direction. Reading the clipboard always needs a tap, and iPhone
   shows its own Paste confirmation on top of that, which is Apple's rule. */
async function sendMyClipboard() {
  if (!navigator.clipboard) {
    $("#compose").focus();
    return toast("Long-press the box, Paste, then Send");
  }
  try {
    // one read() covers both, so iPhone only shows its Paste prompt once
    if (navigator.clipboard && navigator.clipboard.read) {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        const type = it.types.find((t) => t.startsWith("image/"));
        if (type) {
          const blob = await it.getType(type);
          await sendFile(new File([blob], `phone-clipboard-${Date.now()}.png`, { type }), true);
          toast("Image sent to your laptop's clipboard");
          return;
        }
      }
      for (const it of items) {
        if (it.types.includes("text/plain")) {
          const text = await (await it.getType("text/plain")).text();
          if (text.trim()) {
            await sendText(text);
            toast("Sent to your laptop's clipboard");
            return;
          }
        }
      }
      return toast("Your clipboard is empty");
    }
    const text = await navigator.clipboard.readText();
    if (!text || !text.trim()) return toast("Your clipboard is empty");
    await sendText(text);
    toast("Sent to your laptop's clipboard");
  } catch {
    $("#compose").focus();
    toast("Paste into the box, then Send");
  }
}

function addItem(item) {
  items.unshift(item);
  renderItems();
  if (!item.mine && !item.quiet) {
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
      const isImage = /^image\//.test(it.mime || "");
      if (isImage && !it.url) it.url = URL.createObjectURL(new Blob([it.bytes], { type: it.mime }));
      return `<li class="${it.mine ? "mine" : ""}">
        <div class="meta">${esc(who)} &middot; ${isImage ? "image" : "file"}</div>
        ${isImage ? `<img class="thumb" src="${it.url}" alt="${esc(it.name)}">` : ""}
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
    renderPeers();
    toast("Back on");
  } catch (err) {
    if (/code/i.test(err.message)) {
      // the laptop rotated its code, so stop guessing and ask
      state.giveUp = true;
      forget("That code no longer works. Scan the new QR.");
      return;
    }
    if (state.retry === 3 && (await moveToStableName(state.code))) return;
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

async function save(item) {
  const file = new File([item.bytes], item.name, { type: item.mime || "application/octet-stream" });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file] }); return; } catch (e) { if (e && e.name === "AbortError") return; }
  }
  const url = URL.createObjectURL(file);
  const a = document.createElement("a");
  a.href = url; a.download = item.name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* ---------- wiring ---------- */

function init() {
  const params = new URLSearchParams(location.hash.slice(1));
  const fromLink = params.get("code");
  const saved = store.get("code");
  if (fromLink) $("#code").value = fromLink;
  else if (saved) $("#code").value = saved;

  $("#join").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("#code").value.trim();
    const name = store.get("name") || guessName();
    if (!code) return;
    $("#join-error").textContent = "";
    $("#join button").disabled = true;
    $("#join button").textContent = "Connecting...";
    try {
      const info = await connect(code, name);
      state.code = code;
      state.name = name;
      state.giveUp = false;
      state.retry = 0;
      store.set("name", name);
      store.set("code", code);   // so you never type it twice
      if (location.hash) history.replaceState(null, "", location.pathname);
      $("#gate").hidden = true;
      $("#room").hidden = false;
        renderPeers();
      renderItems();
      // keep phones from dozing the socket off
      setInterval(() => { try { state.ws.send(JSON.stringify({ t: "ping" })); } catch {} }, 15000);
    } catch (err) {
      $("#join-error").textContent = err.message;
    } finally {
      $("#join button").disabled = false;
      $("#join button").textContent = "Connect";
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

  $("#latest-copy").addEventListener("click", copyLatest);
  $("#latest-save").addEventListener("click", saveLatest);
  $("#send-clipboard").addEventListener("click", sendMyClipboard);

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
      toast((await copyText(it.data)) ? "Copied" : "Long-press the text to copy");
    } else if (btn.dataset.save !== undefined) {
      save(items[+btn.dataset.save]);
    } else if (btn.dataset.open !== undefined) {
      window.open(items[+btn.dataset.open].data, "_blank", "noopener");
    }
  });
}

/* The QR carries the laptop's IP, which always works for the first scan but
   changes with networks and DHCP leases. The laptop also answers to a stable
   toss-xxxx.local name, so move there when this phone can resolve it. The
   pairing then lives at an address that never changes. */
async function moveToStableName(code) {
  const params = new URLSearchParams(location.hash.slice(1));
  const h = params.get("h") || store.get("host");
  const id = params.get("id") || store.get("id");
  if (h) store.set("host", h);
  if (id) store.set("id", id);
  if (!h || !id || !code || location.protocol !== "http:" || location.hostname === h) return false;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2500);
    const r = await fetch(`http://${h}:${location.port || 80}/ping`, { signal: ctl.signal, cache: "no-store" });
    clearTimeout(t);
    const j = await r.json();
    if (j.id !== id) return false;
    location.replace(`http://${h}:${location.port || 80}/#code=${encodeURIComponent(code)}&h=${h}&id=${id}`);
    return true;
  } catch { return false; }
}

/** Scanning the QR opens this page with the code in the link, and a device
    that paired before has it stored. Either way, connect with no taps. */
async function autoJoin() {
  const code = $("#code").value.trim();
  if (!code) return;
  $("#join button").textContent = "Connecting...";
  if (await moveToStableName(code)) return;
  $("#join button").textContent = "Connecting...";
  $("#join").requestSubmit();
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
