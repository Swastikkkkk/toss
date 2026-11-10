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
  // The QR carries the already-stretched key next to the code (it is no more
  // secret than the code itself, and the #fragment never leaves the phone),
  // so the first scan connects instantly instead of grinding PBKDF2 in JS.
  const pre = store.get("prekey-" + normalized);
  if (pre) {
    store.drop("prekey-" + normalized);
    state.prekeyUsed = { normalized, cacheId };
    store.set(cacheId, pre);
    return { raw: b64ToBytes(pre) };
  }
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
        state.prekeyUsed = null;
        const fp = await fingerprint(state.key);
        const fpEl = $("#fp");
        if (fpEl) fpEl.textContent = fp;
        resolve({ fingerprint: fp, hub: msg.fingerprint, peers: msg.peers });
      } else {
        handle(msg);
      }
    };

    ws.onclose = (ev) => {
      if (!settled && ev.code === 4001 && state.prekeyUsed) {
        store.drop(state.prekeyUsed.cacheId);
        state.prekeyUsed = null;
      }
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
  else if (msg.t === "bridge") { showBridge(JSON.parse(dec.decode(await unseal(state.key, msg)))); }
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

/* ---------- iPhone: Back Tap ----------
   A page can't write your clipboard by itself or run in the background, so on
   iPhone the automatic part is two tiny Shortcuts on Back Tap: double-tap the
   back of the phone and the laptop's clipboard is yours, triple-tap and yours
   is the laptop's. No page open, no tapping Copy. */
function showBridge(b) {
  const base = `http://${b.host}:${b.port}/clip?t=${encodeURIComponent(b.token)}`;
  store.set("bridge-url", base);
  fillBridge();
  // On iPhone this is what turns toss from a page into a share option, so
  // offer it once, right after the first pairing. Never again after that.
  if (isIOS() && store.get("bridge-seen") !== "1") {
    store.set("bridge-seen", "1");
    setTimeout(openSheet, 900);
  }
}

function fillBridge() {
  const url = store.get("bridge-url");
  if (!url) return;
  document.querySelectorAll("[data-url]").forEach((el) => { el.textContent = url; });
}

const isIOS = () => /iPhone|iPad|iPod/i.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

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
  // re-trigger the drop-in animation for every new arrival
  card.hidden = true; void card.offsetWidth; card.hidden = false;
  $("#idle").hidden = true;
  $("#latest-from").textContent = `from ${from || "your laptop"}, just now`;
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
    $("#send").hidden = false; $("#compose").focus();
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
    $("#send").hidden = false; $("#compose").focus();
    toast("Paste into the box, then Send");
  }
}

function addItem(item) {
  items.unshift(item);
  if (items.length > 30) items.length = 30;
  renderItems();
  if (item.mine) return sent(item);
  if (item.quiet) return;
  if (item.kind === "file") showArrivedFile(item);
  else onSync(item.data, item.from);
}

/* A sent thing gets a short beat, the AirDrop "Sent" moment. */
function sent(item) {
  const what = item.kind === "file" ? item.name : isUrl(item.data) ? "Link" : "Text";
  toast(`${what} sent to your laptop`);
  if (navigator.vibrate) navigator.vibrate(18);
}

/* Something sent on purpose (not a clipboard copy) lands on the stage too. */
function showArrivedFile(item) {
  if (/^image\//.test(item.mime || "")) return onSyncImage(item.bytes, item.mime, item.from);
  const blob = new Blob([item.bytes], { type: item.mime || "application/octet-stream" });
  latestClip = { type: "file", blob, mime: blob.type, name: item.name };
  showLatest(item.from);
  $("#latest-img").hidden = true;
  const box = $("#latest-text");
  box.hidden = false;
  box.textContent = `${item.name}  ·  ${size(item.bytes.length)}`;
  $("#latest-copy").hidden = true;
  const saveBtn = $("#latest-save");
  saveBtn.hidden = false;
  saveBtn.textContent = "Save or share";
  saveBtn.classList.add("primary");
  toast(`${item.name} arrived`);
  if (navigator.vibrate) navigator.vibrate(40);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const isUrl = (s) => /^https?:\/\/\S+$/i.test(String(s).trim());
const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;

function renderItems() {
  $("#items").innerHTML = items.map((it, i) => {
    const who = it.mine ? "you sent" : `from ${it.from || "laptop"}`;
    if (it.kind === "file") {
      const isImage = /^image\//.test(it.mime || "");
      if (isImage && !it.url) it.url = URL.createObjectURL(new Blob([it.bytes], { type: it.mime }));
      return `<li class="${it.mine ? "mine" : ""}">
        ${isImage ? `<img src="${it.url}" alt="">` : ""}
        <div class="body">${esc(it.name)}<div class="meta">${esc(who)} &middot; ${size(it.bytes.length)}</div></div>
        <button data-save="${i}">Save</button></li>`;
    }
    const body = isUrl(it.data)
      ? `<a href="${esc(it.data)}" target="_blank" rel="noopener noreferrer">${esc(it.data)}</a>`
      : esc(it.data);
    return `<li class="${it.mine ? "mine" : ""}">
      <div class="body">${body}<div class="meta">${esc(who)}</div></div>
      <button data-copy="${i}">Copy</button></li>`;
  }).join("");
  $("#recent").hidden = items.length === 0;
  $("#recent-label").textContent = `Recent · ${items.length}`;
}

function renderPeers() {
  const n = state.peers.length;
  const first = state.peers[0];
  $("#dot").className = n ? "dot" : "dot off";
  $("#peers").textContent = n === 0 ? "Laptop is asleep"
    : n === 1 ? first.name.replace(/^./, (c) => c.toUpperCase())
    : `${n} devices`;
}

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

/* A big file shows a thin bar under the rings instead of a stream of toasts. */
function progress(id, done, total, name) {
  if (total <= 1) return;
  const pct = Math.round((done / total) * 100);
  $("#idle").hidden = false;
  $("#latest").hidden = true;
  $("#xfer").hidden = done >= total;
  $("#xfer").firstElementChild.style.width = `${pct}%`;
  $("#idle-hint").textContent = done >= total ? "Done" : `${name}  ·  ${pct}%`;
}

/* The page lives on a phone that sleeps, so dropping the socket is normal and
   getting back on quietly is the whole convenience. The first retry is
   immediate, so opening the page feels like it was never closed. */
async function reconnect() {
  if (state.giveUp || state.connecting) return;
  state.connecting = true;
  state.retry++;
  const wait = state.retry === 1 ? 0 : Math.min(500 * 2 ** (state.retry - 2), 8000);
  $("#dot").className = "dot wait";
  $("#peers").textContent = "connecting";
  await new Promise((r) => setTimeout(r, wait));
  let again = false;
  try {
    if (state.giveUp) return;
    await connect(state.code, state.name);
    state.retry = 0;
    renderPeers();
  } catch (err) {
    if (/code/i.test(err.message)) {
      // the laptop rotated its code, so stop guessing and ask
      state.giveUp = true;
      forget("That code no longer works. Scan the new QR.");
      return;
    }
    $("#dot").className = "dot off";
    $("#peers").textContent = "Looking for your laptop";
    if (state.retry === 3 && (await moveToStableName(state.code))) return;
    again = true;
  } finally {
    state.connecting = false;
  }
  if (again) reconnect();
}

function onDisconnected() {
  $("#dot").className = "dot wait";
  $("#peers").textContent = "connecting";
  reconnect();
}

function forget(message) {
  store.drop("code");
  state.giveUp = true;
  try { state.ws && state.ws.close(); } catch {}
  closeSheet();
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

function openSheet() { fillBridge(); $("#sheet").hidden = false; $("#sheet-bg").hidden = false; }
function closeSheet() { $("#sheet").hidden = true; $("#sheet-bg").hidden = true; }

/* ---------- wiring ---------- */

/** Straight into the room and connect. With a remembered code there is no
    pairing screen, no button and no wait. */
async function enter(code) {
  state.code = code;
  state.name = store.get("name") || guessName();
  state.giveUp = false;
  state.retry = 0;
  $("#gate").hidden = true;
  $("#room").hidden = false;
  renderItems();
  fillBridge();
  if (!state.pinging) {
    // keep phones from dozing the socket off
    state.pinging = setInterval(() => { try { state.ws.send(JSON.stringify({ t: "ping" })); } catch {} }, 15000);
  }
  await reconnect();
  if (state.ws && state.ws.readyState === 1) {
    store.set("name", state.name);
    store.set("code", code);   // so you never type it twice
    if (location.hash) history.replaceState(null, "", location.pathname);
    // Moving to the stable toss-xxxx.local name happens after we are
    // connected, never before, so it can never slow down opening the page.
    setTimeout(() => preferStableName(code), 1500);
  }
}

function init() {
  $("#join").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("#code").value.trim();
    if (!code) return;
    $("#join-error").textContent = "";
    const btn = $("#join button");
    btn.disabled = true;
    btn.textContent = "Connecting...";
    try {
      state.name = store.get("name") || guessName();
      await connect(code, state.name);   // a wrong code fails here, on the pairing screen
      state.ws.onclose = null;
      try { state.ws.close(); } catch {}
      await enter(code);
    } catch (err) {
      $("#join-error").textContent = err.message;
    } finally {
      btn.disabled = false;
      btn.textContent = "Connect";
    }
  });

  $("#menu").addEventListener("click", openSheet);
  $("#sheet-close").addEventListener("click", closeSheet);
  $("#sheet-bg").addEventListener("click", closeSheet);
  $("#forget").addEventListener("click", () => forget("Laptop forgotten. Scan its QR to pair again."));

  // Coming back after the phone slept should just work, instantly.
  const revive = () => {
    const dead = !state.ws || state.ws.readyState > 1;
    if (dead && state.code && !state.giveUp) { state.retry = 0; reconnect(); }
  };
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") revive(); });
  window.addEventListener("pageshow", revive);
  window.addEventListener("online", revive);

  // Paste straight into the page, no typing in a box first.
  document.addEventListener("paste", async (e) => {
    if ($("#room").hidden) return;
    if (/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
    const files = [...(e.clipboardData.files || [])];
    if (files.length) { e.preventDefault(); for (const f of files) await sendFile(f); return; }
    const text = e.clipboardData.getData("text");
    if (text && text.trim()) { e.preventDefault(); await sendText(text); }
  });

  $("#write").addEventListener("click", () => {
    const form = $("#send");
    form.hidden = !form.hidden;
    if (!form.hidden) $("#compose").focus();
  });

  // Enter sends, newline needs shift, like every chat box people already know.
  $("#compose").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#send").requestSubmit();
    }
  });

  $("#latest-copy").addEventListener("click", copyLatest);
  document.querySelectorAll("[data-copyurl]").forEach((btn) => btn.addEventListener("click", async () => {
    const url = btn.parentElement.querySelector("[data-url]").textContent;
    toast((await copyText(url)) ? "Link copied" : "Long-press the link to copy");
  }));
  $("#latest-save").addEventListener("click", saveLatest);
  $("#send-clipboard").addEventListener("click", sendMyClipboard);

  $("#send").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#compose").value;
    if (!text.trim()) return;
    await sendText(text);
    $("#compose").value = "";
    $("#send").hidden = true;
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
    }
  });
}

/* The QR carries the laptop's IP, which always works for the first scan but
   changes with networks and DHCP leases. The laptop also answers to a stable
   toss-xxxx.local name, so move there when this phone can resolve it. */
async function moveToStableName(code) {
  const h = store.get("host");
  const id = store.get("id");
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

/* In the background, at most once a day: plenty of Android phones can't
   resolve .local at all, and they shouldn't pay for trying on every open. */
async function preferStableName(code) {
  const last = Number(store.get("stable-tried") || 0);
  if (Date.now() - last < 864e5) return;
  store.set("stable-tried", String(Date.now()));
  await moveToStableName(code);
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
  const params = new URLSearchParams(location.hash.slice(1));
  const fromLink = params.get("code");
  const k = params.get("k");
  if (fromLink && k) {
    const normalized = fromLink.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").join("-");
    store.set("prekey-" + normalized, k.replace(/-/g, "+").replace(/_/g, "/"));
  }
  if (params.get("h")) store.set("host", params.get("h"));
  if (params.get("id")) store.set("id", params.get("id"));
  const code = fromLink || store.get("code");
  if (code) {
    $("#code").value = code;
    enter(code);
  } else {
    $("#gate").hidden = false;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
