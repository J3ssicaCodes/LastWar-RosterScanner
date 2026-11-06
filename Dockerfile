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

COPY vs_rank_reader.py google_vision_ocr.py vs_points.py vs_points_ocr.py vs_ui.js player_identity.py index.html app.py player_list_ocr.py leaderboard_ocr.py media_upload.py google_drive_source.py storm_roster_ocr.py media_ui.js google_drive_picker.js \
     scan_rankings.py ranking_ocr_engine.py extract_ranking_pages.py tested_rankings_adapter.py rank_badge_templates.npz player_profile.js review_validation.js admin_reset.py admin_ui.js ./

EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
