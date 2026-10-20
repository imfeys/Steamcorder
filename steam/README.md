# Steam release kit

Everything needed to put Steamcorder on Steam: the build, the upload script, store artwork and store page text.

## 1. One-time Steamworks setup

1. Join Steamworks at https://partner.steamgames.com. The Steam Direct fee is $100 per app.
2. Create the app. Under **Application type** choose **Software** (it's a utility, not a game).
3. Note the **App ID**, and the **Depot ID** Steam creates for it (usually App ID + 1).
4. In `steam/app_build.vdf`, replace `YOUR_APP_ID` and `YOUR_DEPOT_ID`.
5. **Installation → General**: add a launch option:
   - Executable: `Steamcorder.exe`
   - Launch type: Launch (Default)
   - Operating system: Windows
6. **Depots**: set the depot to Windows, all languages.
7. Download the Steamworks SDK and copy the contents of `tools/ContentBuilder/builder/` (it includes `steamcmd.exe`) into `steam/steamcmd/`. That folder is git-ignored.

## 2. Upload a build

1. Open the latest **Build exe** run in GitHub Actions and download the **Steamcorder-steam** artifact.
2. Unzip it into `steam/content/`, so that `steam/content/Steamcorder.exe` exists.
3. Run `steam\upload.bat YOUR_STEAM_USERNAME` and approve the Steam Guard prompt.
4. In Steamworks, open **SteamPipe → Builds**, pick the new build and set it live on the `default` branch.

## 3. Store artwork (`steam/store/`)

| File | Upload to |
| --- | --- |
| `header_capsule_920x430.png` | Store assets → Header capsule |
| `small_capsule_462x174.png` | Store assets → Small capsule |
| `main_capsule_1232x706.png` | Store assets → Main capsule |
| `vertical_capsule_748x896.png` | Store assets → Vertical capsule |
| `page_background_1438x810.png` | Store assets → Page background (optional) |
| `screenshot_*.png` (5 × 1920×1080) | Screenshots |
| `library_capsule_600x900.png` | Library assets → Library capsule |
| `library_header_920x430.png` | Library assets → Library header |
| `library_hero_3840x1240.png` | Library assets → Library hero |
| `library_logo_1280x720.png` | Library assets → Library logo (transparent) |
| `community_icon_184x184.jpg` | Community assets → Community icon |
| `../icon.ico` | Community assets → Client icon |

## 4. Store page text

**Name:** Steamcorder

**Short description** (up to 300 characters):

> Automatically share your Steam screenshots to Discord. Press F12 in any game and Steamcorder posts the screenshot to your server's channel a few seconds later. No bot, no manual uploading. It runs quietly in the tray.

**About this software:**

> Steamcorder turns any Discord channel into a live screenshot feed for you and your friends.
>
> Take a screenshot in-game and Steamcorder spots it the moment Steam saves it, waits until the file is complete, and posts it to your Discord server through a webhook.
>
> **Features**
> - Automatic uploads of every new Steam screenshot
> - Works with any Discord server through a webhook. No bot needed
> - Runs quietly in the system tray and can start with Windows
> - Waits for files to finish saving and retries if Discord is busy
> - Optionally deletes screenshots after they're uploaded
> - Supports PNG, JPG, GIF, BMP and WebP
>
> **Setup takes two minutes:** turn on uncompressed screenshots in Steam, choose that folder in Steamcorder, paste a Discord webhook URL, and click Start watching.

**Suggested tags:** Utilities, Software, Social, Screenshots, Free to Play (if free)

**Genre:** Utilities

**System requirements (minimum):**
- OS: Windows 10 64-bit
- Processor: Any 64-bit processor
- Memory: 512 MB RAM
- Storage: 300 MB available space
- Network: Broadband internet connection

## 5. Before launch

- [ ] App ID and depot ID filled in `app_build.vdf`
- [ ] Build uploaded and set live on `default`
- [ ] Store artwork and five screenshots uploaded
- [ ] Price set (or Free), release date set
- [ ] Store page and build submitted for Valve review (takes a few days)
- [ ] Pressed **Play** from a Steam library on a clean PC to check that it launches
