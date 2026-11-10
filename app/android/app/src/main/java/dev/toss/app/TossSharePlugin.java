package dev.toss.app;

import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.util.Base64;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.ArrayList;

/**
 * toss in Android's share menu. Share a photo, a link, a PDF or a bunch of
 * files from any app, pick toss, and the page sends them to the laptop. This
 * only turns the share intent into { texts, files } for the page; the page does
 * the encrypting and sending, exactly as it does for a file you pick by hand.
 */
@CapacitorPlugin(name = "TossShare")
public class TossSharePlugin extends Plugin {
    private static final long MAX_BYTES = 200L * 1024 * 1024;
    private static JSObject pending;      // a share that arrived before the page was listening
    private static TossSharePlugin live;

    @Override
    public void load() {
        live = this;
    }

    /** The page asks once on start, for a share that cold-started the app. */
    @PluginMethod
    public void getShared(PluginCall call) {
        JSObject data = pending;
        pending = null;
        call.resolve(data != null ? data : new JSObject());
    }

    static void handle(android.content.Context ctx, Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) return;

        JSArray texts = new JSArray();
        JSArray files = new JSArray();

        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        ArrayList<Uri> uris = new ArrayList<>();
        if (Intent.ACTION_SEND.equals(action)) {
            Uri one = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (one != null) uris.add(one);
        } else {
            ArrayList<Uri> many = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (many != null) uris.addAll(many);
        }
        // A link shared with a picture (a tweet, a product) is both; when there
        // is no file, the text is the whole point.
        if (text != null && text.length() > 0 && uris.isEmpty()) texts.put(text.toString());

        ContentResolver cr = ctx.getContentResolver();
        for (Uri uri : uris) {
            try {
                JSObject f = new JSObject();
                f.put("name", displayName(cr, uri));
                String mime = cr.getType(uri);
                f.put("mime", mime != null ? mime : "application/octet-stream");
                f.put("data", Base64.encodeToString(read(cr, uri), Base64.NO_WRAP));
                files.put(f);
            } catch (Exception ignored) {
                // a file we are not allowed to read, or too big: skip it, send the rest
            }
        }
        if (texts.length() == 0 && files.length() == 0) return;

        JSObject data = new JSObject();
        data.put("texts", texts);
        data.put("files", files);
        // so the same share is not handled again when the activity is recreated
        intent.setAction(Intent.ACTION_MAIN);

        if (live != null && live.hasListeners("shared")) live.notifyListeners("shared", data);
        else pending = data;
    }

    private static String displayName(ContentResolver cr, Uri uri) {
        try (Cursor c = cr.query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) {
                String n = c.getString(0);
                if (n != null && !n.isEmpty()) return n;
            }
        } catch (Exception ignored) {
        }
        String last = uri.getLastPathSegment();
        return last != null ? last : "shared";
    }

    private static byte[] read(ContentResolver cr, Uri uri) throws Exception {
        try (InputStream in = cr.openInputStream(uri)) {
            if (in == null) throw new Exception("unreadable");
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[64 * 1024];
            long total = 0;
            int n;
            while ((n = in.read(buf)) > 0) {
                total += n;
                if (total > MAX_BYTES) throw new Exception("too big");
                out.write(buf, 0, n);
            }
            return out.toByteArray();
        }
    }
}
