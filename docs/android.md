# Android build and installation

Complete the repository's first-time setup before building Android:

```bash
python3 scripts/bootstrap.py
```

The bootstrap installs and exposes the required JDK 21 commands, including
`java` and `keytool`.

## Remaining requirements

- Android SDK Platform 36
- Android Platform Tools
- Android Build Tools 35

After installing the Android SDK, rerun the bootstrap once. It detects the SDK,
configures `android/local.properties`, and exposes `adb` through
`~/.local/bin`:

```bash
python3 scripts/bootstrap.py --verify-only
```

## Debug build

```bash
npm run android:debug
```

The APK is written to:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

## Release signing

Create and securely back up a private keystore outside the repository:

```bash
mkdir -p ~/.local/share/mandarin-tone-trainer
"$HOME/.local/bin/keytool" -genkeypair \
  -keystore ~/.local/share/mandarin-tone-trainer/release.jks \
  -alias mandarin-tone-trainer \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000
```

Copy `keystore.properties.example` to the ignored `keystore.properties` file.
Enter the absolute keystore path, alias, and passwords.

```bash
npm run android:release
```

The signed APK is written to:

```text
android/app/build/outputs/apk/release/app-release.apk
```

Never commit the keystore or `keystore.properties`. Back up the keystore and
credentials together. Android updates must be signed with the same key.

## Install on a device

Enable Developer options and USB debugging, connect the device, and run:

```bash
"$HOME/.local/bin/adb" install android/app/build/outputs/apk/release/app-release.apk
```

For later releases, increment `versionCode` in
`android/app/build.gradle`, rebuild with the same key, and update in place:

```bash
"$HOME/.local/bin/adb" install -r android/app/build/outputs/apk/release/app-release.apk
```

Without USB debugging, transfer the APK to the device and open it from the
Files app. Android may ask you to allow **Install unknown apps** for Files.

The first use of **Record me** requests microphone permission. All listening
and quiz features work without that permission.

If `resources/logo.svg` changes, regenerate launcher and splash resources with
Android Studio's Image Asset tools.
