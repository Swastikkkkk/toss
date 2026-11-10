# toss app (Android and iPhone)

The phone app is the **same page** as the web version, in a native shell
([Capacitor](https://capacitorjs.com)). There is one UI and one set of
features, in `toss/web/`, and three ways to run it:

| | where the page comes from | what native adds |
| --- | --- | --- |
| a browser on your wifi | served by the laptop | nothing, it is a web page |
| the Android app | bundled in the app | Share menu, clipboard without a tap, QR scanner, save/share files, notifications |
| the iPhone app | bundled in the app | clipboard without a tap, QR scanner, save/share files, notifications |

`capacitor.config.json` points `webDir` at `../toss/web`, so a change to the
page shows up everywhere the next time you sync. Nothing is copied by hand
and nothing can drift. Code that only makes sense in the app lives in
`toss/web/native.js`, and in a browser that module does nothing.

The laptop side does not change: clone the repo, run `toss`, and pair the app
by scanning the QR on the laptop, the same way a browser does.

## Build for Android (Windows, macOS or Linux)

Needs Node 18+, JDK 17 and the Android SDK (Android Studio installs both).

```bash
cd app
npm install
npx cap sync android
cd android
./gradlew assembleDebug        # gradlew.bat on Windows
```

The APK is `android/app/build/outputs/apk/debug/app-debug.apk`. Copy it to the
phone and open it, or with the phone plugged in:

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

Or `npx cap open android` and press Run in Android Studio.

## Build for iPhone (needs a Mac)

Apple only builds iPhone apps on a Mac with Xcode. The project is already
generated in `ios/`:

```bash
cd app
npm install
npx cap sync ios               # also runs pod install
npx cap open ios               # then pick your iPhone and press Run
```

## What the app adds over the web page

- **Share to toss** (Android): toss is in every app's Share menu. Share a
  photo, a link or a stack of files and they go to the laptop. Handled by
  `TossSharePlugin.java`; the page does the encrypting and sending.
- **No "Tap to copy"**: what you copy on the laptop goes straight onto the
  phone's clipboard while the app is open.
- **Scan to pair** inside the app (Google's scanner on Android, so it needs no
  camera permission).
- **Save or share** received files with the system share sheet.

## Not built yet

- A share extension on iPhone (a separate Xcode target; it needs a Mac).
  Until then the Shortcut described in the app's ··· menu does the same job.
- Staying connected with the app closed (an Android foreground service). Right
  now it reconnects instantly when you open it, and the laptop keeps
  everything you missed for 12 hours.
