Red Sun
Turn any photo into a sharp, color-indexed MS Paint style bitmap. Poster sized, batch friendly.

OPEN THE APPLICATION
--------------------
Extract the complete ZIP. Keep every launcher beside the `app` folder.

- macOS (Intel and Apple Silicon): double-click "Open Red Sun — macOS.app".
- Windows x64: double-click "Open Red Sun — Windows x64.exe".
- Windows ARM64: double-click "Open Red Sun — Windows ARM64.exe".
- Linux x86_64: double-click "Open Red Sun — Linux x86_64.AppImage".
- Linux ARM64: double-click "Open Red Sun — Linux ARM64.AppImage".

If the launcher for your system is not in this folder, this is a partial build for the
platforms listed above only. The source is at https://github.com/ (Red Sun repository):
run.sh / run.ps1 there start the same application with uv or Python 3.10+.

The first launch opens a setup page in your browser and downloads a private Python runtime and
the locked image library (about 40 MB) into your application-data folder. No system Python,
Node.js, compiler or terminal command is needed. First setup needs network access once; later
launches are offline and instant. The setup page then becomes the Red Sun page.

USING IT
--------
Browse for an image or a folder, pick a palette and size, click Process. Outputs are saved as
indexed PNG (or BMP) in a "red_sun" folder next to your images. Originals are never modified.
Quit from the button at the bottom of the page.

SECURITY PROMPTS
----------------
These launchers are not code-signed yet.
- macOS Gatekeeper may say the developer cannot be verified: right-click the .app, choose Open,
  then Open again. This is only needed once.
- Windows SmartScreen may show "Windows protected your PC": click More info, then Run anyway.
- Linux: if double-click does nothing, open the file's Properties > Permissions and enable
  "Allow executing file as program", then double-click it again.

RECOVERY
--------
The setup page shows a stable error code, a Retry button when retrying is safe, and the exact
log location. BOOT-NETWORK means the one-time download needs internet. LAUNCH-ROOT means the
launcher was moved away from the `app` folder: extract the ZIP again.

Application data and logs:
- macOS:   ~/Library/Application Support/Red Sun
- Windows: %LOCALAPPDATA%\Red Sun
- Linux:   ~/.local/share/Red Sun
Deleting that folder resets the runtime; the next launch downloads it again.

Copyright (c) 2026 Goosnav LLC. All rights reserved. See LICENSE.txt.
