# Last War Roster Scanner v2

A browser based Last War alliance toolkit. Created by Lady Jess. The complete existing roster scanner now lives under the Roster Scanner tab, alongside a new Player List tab ready for the next feature.

## Start the app

Visit the Render hosted website, or open `index.html` in a modern Chrome or Edge browser. No build step is required. An internet connection is needed to load Tesseract.js for OCR.

## Workspace tabs

- **Roster Scanner:** The complete existing screenshot scanner, Desert Storm and Canyon Storm Event Manager, assignments, map generator, and OCR correction dictionary.
- **Player List:** A separate workspace reserved for upcoming player list features. No player list functionality has been added yet.
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

**Player List:** the new importer sends selected screenshots or screen recordings to the Render service for OCR. The server keeps files only in a temporary directory during scanning, then deletes them. The user reviews names, in-game ranks, power values and HQ levels before syncing. Confirmed player details are saved to PostgreSQL and shared between browsers.

The backend automatically creates a PostgreSQL table named `"player list"` (and an import audit table `player_list_imports`) on startup, using Render environment variables. Configure either `DATABASE_URL` or the equivalent `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` variables. Other common PostgreSQL environment variable names are supported too. Never place secrets in `index.html` or commit them to GitHub.

For safer public access, set `PLAYER_LIST_UPLOAD_KEY` as a Render environment variable; authorized uploaders enter it in the Player List tab. Viewing the shared roster does not require the key. The key is never sent to the browser as a page variable, and is checked on the server for uploads and imports.

After a video or screenshot batch is OCR scanned, review the extracted entries and select **Sync**. Existing names are updated; new names are marked **New player**. When an import is explicitly marked as the complete roster, players not seen in that import are marked **Possibly removed** without being deleted. Incomplete screenshots should not be marked as complete.

Browser OCR and imported video OCR are different systems, so this new backend does not alter the original Roster Scanner. 

## Player List endpoints

- `GET /api/player-list/health` checks the database connection without exposing credentials.
- `GET /api/player-list` returns the current member directory.
- `POST /api/player-list/scan` accepts a screenshot batch or one video; `GET /api/player-list/scan/{id}` returns OCR progress and review candidates.
- `POST /api/player-list/import` saves reviewed players in a transaction.
- `GET /api/player-list/export.csv` exports the current shared list.

A scan job processes up to 150 sampled frames per video, with a 150 MB upload limit and a seven minute duration limit. Server OCR uses the English Tesseract language pack, so names containing scripts not represented by that pack will need manual correction or future language packs. Scans run one at a time to conserve memory on Render's free service.

## Notes

This version includes the Canyon Storm faction banner OCR layout correction. OCR accuracy can vary by screenshot dimensions, compression, and game language; manually review the resulting roster before using it for an event.

This is a private project and is not affiliated with the Last War game publisher.
