# Last War Roster Scanner v2

A browser based Last War alliance toolkit. Created by Lady Jess. The complete existing roster scanner now lives under the Roster Scanner tab, alongside a new Player List tab ready for the next feature.

## Start the app

Visit the Render hosted website, or open `index.html` in a modern Chrome or Edge browser. No build step is required. An internet connection is needed to load Tesseract.js for OCR.

## Workspace tabs

- **Roster Scanner:** The complete existing screenshot scanner, Desert Storm and Canyon Storm Event Manager, assignments, map generator, and OCR correction dictionary.
- **Player List:** Import player screenshots, profile screenshots, or a scrolling video; review OCR results and synchronize the shared MySQL player list.
- The selected tab is remembered in this browser. Switching tabs does not reset in memory roster data or saved settings.

## Features

- Scan Last War roster screenshots to identify Team A and Team B, player role, name, power, and participant or substitute status.
- Support differently positioned team selectors, including Canyon Storm screenshots with the faction banner, plus a German language interface example.
- Review and edit extracted entries, learn OCR name corrections, and detect potential duplicates.
- Plan up to 20 active participants per team, with separate Phase 1 and Phase 2 assignments and player swaps.
- Generate downloadable assignment maps, including embedded Desert Storm and Canyon Storm templates.
- Configure event priority slots and map label positions, with backup and restore of event settings.
- Switch between English, French, German, Italian, Danish, and Spanish. Generated maps, CSV and clipboard output remain in English.
- Toggle light and dark themes.

## Repository layout

- `index.html`: Entire web app, including styles, UI, application logic, and embedded map templates.
- `Dockerfile`: Python web service with FastAPI, Tesseract OCR and FFmpeg for the Player List importer.
- `config/default_events.json`: Reference backup for the built in Desert Storm and Canyon Storm defaults. The app also embeds these settings in `index.html`, so this JSON file is provided for inspection and manual import.

## Storage and privacy

**Roster Scanner:** screenshots are read locally in the browser, not uploaded to the server. Event configurations, OCR name dictionary, and preferences remain in browser storage.

**Player List:** the new importer sends selected screenshots or screen recordings to the Render service for OCR. The server keeps files only in a temporary directory during scanning, then deletes them. The user reviews names, in-game ranks, power values and HQ levels before syncing. Confirmed player details are saved to MySQL and shared between browsers.

The backend automatically creates a MySQL table named `"player list"` (and an import audit table `player_list_imports`) on startup, using Render environment variables. Configure either `DATABASE_URL` or the equivalent `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` variables. Other common MySQL environment variable names are supported too. Never place secrets in `index.html` or commit them to GitHub.

Player List does not require an upload key. Any visitor who can reach the website can upload screenshots or a video, and can synchronize scanned or reviewed players to the shared database. This keeps the user workflow simple, but also means edits are public. If you need to restrict changes in the future, implement a simple sign-in or invite system rather than a shared upload key.

After a video or screenshot batch is OCR scanned, review the extracted entries and select **Sync**. Existing names are updated; new names are marked **New player**. When an import is explicitly marked as the complete roster, players not seen in that import are marked **Possibly removed** without being deleted. Incomplete screenshots should not be marked as complete.

Browser OCR and imported video OCR are different systems, so this new backend does not alter the original Roster Scanner. 

## Player List endpoints

- `GET /api/player-list/health` checks the database connection without exposing credentials.
- `GET /api/player-list` returns the current member directory.
- `POST /api/player-list/scan` accepts a screenshot batch or one video; `GET /api/player-list/scan/{id}` returns OCR progress and review candidates.
- `POST /api/player-list/import` saves reviewed players in a transaction.
- `GET /api/player-list/export.csv` exports the current shared list.

A scan job processes up to 96 adaptive sampled frames per video, with a 150 MB upload limit and a seven minute duration limit. Server OCR uses the English Tesseract language pack, so names containing scripts not represented by that pack will need manual correction or future language packs. Scans run one at a time to conserve memory on Render's free service.


## Faster video scans and temporary media

Player List uploads are streamed into a temporary directory on Render. Once the upload returns a scan job ID, OCR runs on the server in a background worker. The video, screenshots, and any extracted video frames are deleted from that temporary directory after scanning completes or fails. Results from the shared Media Upload route are staged in MySQL until approved. The shared MySQL player list is not changed by merely scanning.

Android or iOS may suspend an upload when a phone locks. Keep the page open **until the upload finishes and the server confirms it is scanning**. After that acknowledgement, locking the screen will not stop the server's worker. The shared Media Upload route stores job status and pending review rows in MySQL, so they can be recovered after a page reload. Jobs can be lost if Render restarts its process, and a long-running scan is subject to Render's free-service runtime limits; if that happens, upload the source again.

The OCR engine remains free open source Tesseract running within Render. Videos use adaptive frame sampling, repeat-card skipping, and a dedicated member-list detector to avoid unnecessary OCR work. Highly rapid scrolling can still cause missed players and should be reviewed before a complete-roster import.

## Notes

This version includes the Canyon Storm faction banner OCR layout correction. OCR accuracy can vary by screenshot dimensions, compression, and game language; manually review the resulting roster before using it for an event.

This is a private project and is not affiliated with the Last War game publisher.

## Individual player profiles

The Player List uploader accepts individual Player Profile screenshots as well as
Member List screenshots and one MP4/MOV/WebM screen recording in the same batch.
It detects the blue `Lv.XX NAME` profile header, the power value in the first
stat box, and the `R1` through `R5` shield next to the alliance label. Player
Profile screenshots are particularly useful for R5 leaders who appear above
the member search bar and should not be counted as scrolling member cards.

Profile entries are merged with players detected in the scrolling list using
exact normalized player names. All new uploads through the common Media Upload button require review and approval before changing the official Player List.
Profile-only uploads never flag other players as removed. Recordings can be
combined with extra profile screenshots, and the complete-list setting must
be confirmed before flagging missing members. Only the Player List tab and
its backend import pipeline use the shared database.

## MySQL database setup

The Player List backend uses the existing cPanel MySQL database via environment variables `DB_HOST`, `DB_PORT` (normally 3306), `DB_USER`, `DB_PASSWORD`, and `DB_NAME`. At startup it creates the **`player list`** and **`player_list_imports`** tables, if they do not already exist, with no destructive migration. Render must be allowed to connect to the remote MySQL host and the database user needs permission to create tables. Use `GET /api/player-list/health` to confirm `table_ready: true`. Database credentials remain in Render settings, not source control.

## Player List video portraits and birthdays

* Portrait thumbnails are cropped from **video recordings only**, including the R5 leader portrait in the member list header. No screenshot is used as a portrait source.
* Crops are compressed as WebP and stored in the existing MySQL `player list` table. They persist through Render redeploys.
* The optional birthday field is entered manually through a calendar icon in Player List and saved by `PATCH /api/player-list/{id}/birthday`. Scanning never invents birthdays or clears previously entered birthdays.
* Existing MySQL player tables are upgraded in place with `birthday`, `avatar_image`, and `avatar_updated_at` columns at startup. Profiles and incomplete screenshots without a video retain existing portraits.

## Shared Media Upload and approval workflow (current)

All new uploads begin with the **Media Upload** button in the Alliance Tools header.
Choose **Storm Roster** or **Player List**; **VS Scores** is intentionally disabled
until that module is developed. You can send screenshots or one screen recording
and optional screenshots in the same batch. After the server accepts the upload,
the OCR job runs on Render even if the phone screen locks. Raw uploads and extracted
video frames are kept in a temporary server directory and deleted after each job.

Three new MySQL tables are automatically initialized, using the existing Render
database configuration:

* `media_scan_jobs` stores scan status and progress across page reloads. A server
  restart may interrupt an active scan, but completed results remain available.
* `storm roster` stores the **latest Storm scan only**. A successful scan replaces
  the previous event snapshot. Roster Scanner loads that snapshot automatically,
  preserving its existing planner, Event Manager and phase assignments.
* `roster upload` stores extracted Player List records **awaiting approval**.
  Pending changes are displayed in an editable review panel above the Player List
  directory, and this panel is hidden when no records are pending.

Approving Player List changes writes the accepted records to the official `player list`
table and removes the reviewed entries from `roster upload`. Unchecked detections
are dismissed. A full capture flag can only mark players possibly removed after
review; incomplete captures do not affect other members. No Player List upload now
writes directly to the official table during scanning.

Player List is grouped under collapsible R5 through R1 rank headings rather than
repeating the rank in every row. Last Seen is not shown or exported in the Player
List CSV. Birthday and portrait data are retained across scans as before.

Notifications are shown inside the web app when a scan finishes or when a pending
Player List review becomes available. Opening or resuming the app refreshes the
status. This is not a native Android/iOS push notification system.

## Leaderboard OCR uploads (Power, Kills, Donation)

The shared **Media Upload** dialog accepts only PNG, JPEG, or WebP for Storm Roster. Player List accepts leaderboard screenshots and one video (plus screenshots). VS Scores is intentionally disabled.

Player List leaderboard scans use `leaderboard_ocr.py`, a shared Python pipeline: visual tab selection, video pause detection, deduplicated full resolution pages, calibrated row borders, complete row OCR, alignment, gap validation, targeted transition recovery, and an auditable staged review. The scanner rejects clipped rows; it never fills missing positions to reach a target count. Positions are internal metadata, not player attributes. Power, Kills, and Donations are stored as integer columns. The original Power (M) profile field is retained for compatibility. Existing birthdays and HQ fields are never erased by missing OCR values.

**Processing and privacy:** Uploads are temporarily held on the Render instance, then deleted when the scan ends. MySQL stores completed scan jobs, OCR diagnostics, staged review entries, and approved player data. The scan job continues on the server after the user closes or locks the phone, subject to Render instance restarts. Pending rows are only committed after explicit approval. Unchecked rows stay pending; use Dismiss to remove unwanted OCR candidates.

**Dependencies:** OpenCV, NumPy, SciPy, Pillow, Tesseract, pytesseract, and FFmpeg are free, locally executed dependencies. The optional file `rank_badge_templates.npz` may be added when trustworthy badge samples exist; until then, ambiguous rank badges are left unresolved for user correction.

**Acceptance test limitation:** The available earlier sample MP4 (`1000024431.mp4`) captures the older Member List interface, not the approximately 4,149 frame Power leaderboard recording described in the feature specification. The full 89 position regression and missing position 77 recovery cannot be claimed as verified until that leaderboard recording is provided. Tests on a controlled leaderboard fixture cover tab selection, pitch and full row recognition, uncertain OCR handling, and video pause extraction.
