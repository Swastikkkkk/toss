/* toss in the app. The same page runs in three places: a browser on your
   wifi, the Android app and the iPhone app. In a browser this whole module is
   inert (`app` is null) and the page uses web APIs. Inside the app it hands the
   things a web page is not allowed to do to native code: writing the
   clipboard without a tap, scanning the pairing QR, saving and sharing files,
   receiving what you share from other apps, and notifications. */

const C = window.Capacitor;
const isApp = !!(C && C.isNativePlatform && C.isNativePlatform());

function plugin(name) {
  if (!isApp) return null;
  try { return C.registerPlugin ? C.registerPlugin(name) : C.Plugins[name]; } catch { return null; }
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export const app = !isApp ? null : {
  platform: C.getPlatform(),   // "android" or "ios"

  async copyText(text) {
    await plugin("Clipboard").write({ string: text });
    return true;
  },

  async copyImage(blob) {
    const b64 = await blobToBase64(blob);
    await plugin("Clipboard").write({ image: `data:${blob.type || "image/png"};base64,${b64}` });
    return true;
  },

  /** { type: "text", text } or { type: "image", blob } or null */
  async readClipboard() {
    const r = await plugin("Clipboard").read();
    if (!r || !r.value) return null;
    if (/^image\//.test(r.type || "") || /^data:image\//.test(r.value)) {
      const m = /^data:([^;]+);base64,(.*)$/.exec(r.value);
      const mime = m ? m[1] : r.type || "image/png";
      return { type: "image", blob: new Blob([base64ToBytes(m ? m[2] : r.value)], { type: mime }) };
    }
    return { type: "text", text: r.value };
  },

  /** Save to the app's files, then open the system share sheet:
      Save to Files / Photos / Drive, or straight into WhatsApp. */
  async shareFile(file) {
    const fs = plugin("Filesystem");
    const name = file.name.replace(/[\\/:*?"<>|]+/g, "_") || "toss-file";
    const res = await fs.writeFile({ path: name, data: await blobToBase64(file), directory: "CACHE" });
    try {
      await plugin("Share").share({ files: [res.uri], title: name });
    } catch (e) {
      if (!/cancel/i.test(String(e && e.message))) throw e;
    }
  },

  /** Opens the camera, returns the text in the QR or null. */
  async scanQR() {
    const scanner = plugin("BarcodeScanner");
    if (C.getPlatform() === "android") {
      // Google's scanner UI: no camera permission, nothing to grant
      try {
        const { available } = await scanner.isGoogleBarcodeScannerModuleAvailable();
        if (!available) await scanner.installGoogleBarcodeScannerModule();
      } catch {}
    } else {
      const { camera } = await scanner.requestPermissions();
      if (camera !== "granted" && camera !== "limited") throw new Error("Camera access is off for toss.");
    }
    const { barcodes } = await scanner.scan({ formats: ["QR_CODE"] });
    return barcodes && barcodes[0] ? barcodes[0].rawValue : null;
  },

  /** Things shared into toss from other apps' share menus.
      cb gets { texts: [string], files: [File] }. */
  onShared(cb) {
    const share = plugin("TossShare");
    if (!share) return;
    const deliver = (data) => {
      if (!data) return;
      const texts = (data.texts || []).filter((t) => t && t.trim());
      const files = (data.files || []).map((f) =>
        new File([base64ToBytes(f.data)], f.name || "shared", { type: f.mime || "application/octet-stream" }));
      if (texts.length || files.length) cb({ texts, files });
    };
    share.addListener("shared", deliver);
    share.getShared().then(deliver).catch(() => {});
  },

  async notify(title, body) {
    const n = plugin("LocalNotifications");
    try {
      const p = await n.checkPermissions();
      if (p.display !== "granted" && (await n.requestPermissions()).display !== "granted") return;
      await n.schedule({ notifications: [{ id: Date.now() % 2147483647, title, body }] });
    } catch {}
  },
};
