# Steamcorder

Automatically post your Steam screenshots to a Discord channel.

Take a screenshot in-game with F12 and it appears in Discord a few seconds later. You don't need Python or any other install: download one `.exe` and run it.

<p align="center">
  <img src="docs/home.png" width="380" alt="Steamcorder main screen">
  <img src="docs/settings.png" width="380" alt="Steamcorder settings">
</p>

## Download

Get **`Steamcorder.exe`** from the [latest release](https://github.com/imfeys/Steamcorder/releases/latest) and double-click it.

> Windows SmartScreen may warn you because the app isn't code-signed. Click **More info → Run anyway**.

## Setup (2 minutes)

1. **Turn on uncompressed screenshots in Steam.**
   Steam → Settings → In Game → enable *Save an uncompressed copy of my screenshots* and pick a folder.
2. **Choose that folder in Steamcorder.**
   Settings → *Browse*.
3. **Create a Discord webhook.**
   In Discord: Server Settings → Integrations → Webhooks → *New Webhook* → *Copy Webhook URL*.
   Paste it into Steamcorder and click *Test*.
4. **Click _Start watching_.** That's it.

Settings save automatically. Steamcorder remembers whether it was watching and picks up where it left off next time.

## Options

| Option | What it does |
| --- | --- |
| Upload delay | Extra wait after a screenshot is saved, before it's posted. |
| Delete after uploading | Removes the local file once Discord has it. |
| Keep running in the tray | Closing the window hides it to the tray instead of quitting. |
| Start with Windows | Launches quietly in the tray when you sign in. |

Settings are stored in `%APPDATA%\Steamcorder\config.json`.

**Large screenshots:** Discord limits upload size per server (10 MB without boosts). Uncompressed 4K PNGs can exceed that. If one does, Steamcorder tells you in the activity list.

## Building it yourself

Every push to `main` builds the `.exe` with GitHub Actions. Pushing a version tag (for example `git tag v1.0.0 && git push --tags`) publishes it as a release.

To build locally on Windows (needs Python 3.9+), run:

```bat
build.bat
```

The result is `dist\Steamcorder.exe`. To run from source instead: `pip install -r requirements.txt`, then `python Steamcorder.pyw`.

## Support

Questions or bugs: [Discord](https://discord.gg/dMvCH93sYX) or [GitHub Issues](https://github.com/imfeys/Steamcorder/issues).

## License

[GPL-3.0](LICENSE)
