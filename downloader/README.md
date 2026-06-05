# Photo Downloader

Downloads photos from your Nextcloud library to an external SSD.
Open `http://localhost:3100` in your browser to use it.

---

## One-time setup

### 1. Allow Docker to see your external drives

Open **Docker Desktop → Settings → Resources → File Sharing**
and add `/Volumes` to the list. Click Apply & Restart.

> This is required so the app can detect and write to SSDs plugged into your Mac.

### 2. Create your credentials file

Inside the `downloader/` folder, run:

```bash
cp downloader/.env.template downloader/.env
```

Then edit `downloader/.env`:

```
NEXTCLOUD_URL=https://divyanshu-palak-wedding.space
NEXTCLOUD_USERNAME=admin
NEXTCLOUD_PASSWORD=your_password
MAX_CONCURRENT_DOWNLOADS=4
```

This file is gitignored and stays on your machine only.

---

## Start the downloader

From the `remote-fss` folder:

```bash
docker compose -f downloader/docker-compose.yml --env-file downloader/.env up -d
```

Then open **http://localhost:3100** in your browser.

The main media stack (Nextcloud + Immich) is completely separate — starting or stopping the downloader does not affect it.

---

## Stop the downloader

```bash
docker compose -f downloader/docker-compose.yml down
```

Downloaded files stay on your SSD. Next time you start the app and plug in the same SSD, it will offer to resume where it left off.

---

## How to use

1. **Select your SSD** — plug it in and pick it from the list
2. **Select a folder** — browse your Nextcloud and pick the folder to download
3. **Download** — watch progress in real time; adjust parallel downloads (1–8) to control speed

### If the SSD gets unplugged mid-download
The download pauses automatically. Plug the SSD back in and it resumes on its own.

### If you plug in the wrong SSD
The app warns you that a previous download was started on a different drive, so you don't accidentally restart progress.

### Already downloaded files
Files already present on the SSD with the correct size are skipped instantly — safe to re-run after an interrupted download.

---

## Troubleshooting

**"No drives found"** — `/Volumes` is not added to Docker Desktop file sharing (see step 1 above).

**"Nextcloud unreachable"** in the status bar — check your credentials in `downloader/.env` are correct.

**Logs:**
```bash
docker compose -f downloader/docker-compose.yml logs -f downloader-api
```
