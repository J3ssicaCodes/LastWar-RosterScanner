FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg tesseract-ocr tesseract-ocr-eng libglib2.0-0 libgl1 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY train_manager.py train_manager.js player_merge.py player_performance.py player_performance.js vs_rank_reader.py google_vision_ocr.py vs_points.py vs_points_ocr.py vs_ui.js player_identity.py index.html app.py player_list_ocr.py leaderboard_ocr.py media_upload.py google_drive_source.py storm_roster_ocr.py media_ui.js google_drive_picker.js \
     scan_rankings.py ranking_ocr_engine.py extract_ranking_pages.py tested_rankings_adapter.py rank_badge_templates.npz player_profile.js review_validation.js admin_reset.py admin_ui.js ./
COPY workspace_migration.py workspace_bootstrap.py scoped_db.py workspace_security.py alliance_auth.py alliance_pages.py alliance_operations.py operations.js \
     alliance_claims.py alliance_leadership.py alliance_push.py alliance_analytics.py alliance_viewer.html alliance_ui.js alliance_account.html alliance_account.js \
     workspace-context.js alliance_help.js service-worker.js alliance-icon-192.png alliance-icon-512.png ./

COPY help-images ./help-images

EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
