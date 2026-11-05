"""Access one user-selected Google Drive file with short-lived OAuth credentials.

Tokens remain in the scan worker's memory only. Never log, persist, cache or
return Drive access tokens. Google Drive originals are only moved to Trash
after a completed, successfully staged scan and explicit user opt-in.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import request, error
from urllib.parse import quote, urlsplit

FILE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,256}$')
MAX_BYTES = 150 * 1024 * 1024
MIME_SUFFIX = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'video/mp4': '.mp4',
    'video/quicktime': '.mov',
    'video/webm': '.webm',
    'video/x-m4v': '.m4v',
}
ALLOWED_EXTENSIONS = set(MIME_SUFFIX.values()) | {'.jpeg'}


class SafeGoogleRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        hostname = parsed.hostname or ''
        approved = (hostname == 'googleapis.com'
                    or hostname.endswith('.googleapis.com')
                    or hostname == 'googleusercontent.com'
                    or hostname.endswith('.googleusercontent.com'))
        if parsed.scheme != 'https' or not approved:
            raise ValueError('Drive transfer redirected to an unexpected host.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_google_opener = request.build_opener(SafeGoogleRedirect())


def _request(url: str, token: str, method='GET', body=None):
    if len(token) > 8192 or not token or '\n' in token or '\r' in token:
        raise ValueError('Google Drive authorization is invalid. Reconnect and retry.')
    if not url.startswith('https://www.googleapis.com/drive/v3/files/'):
        raise ValueError('Invalid Google Drive request.')
    headers = {'Authorization': 'Bearer ' + token}
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body).encode('utf-8')
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        return _google_opener.open(req, timeout=45)
    except error.HTTPError as exc:
        if exc.code in (401,403):
            raise ValueError('Google Drive access expired or was denied. Reconnect and retry.') from None
        if exc.code == 404:
            raise ValueError('Google Drive file was not found or is no longer shared.') from None
        raise ValueError('Google Drive could not provide the selected file.') from None
    except error.URLError:
        raise ValueError('Could not connect to Google Drive. Try again.') from None


def download_selected_file(file_id: str, token: str, destination: str, directory: Path):
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise ValueError('Invalid Google Drive file selection.')
    file_url = 'https://www.googleapis.com/drive/v3/files/' + quote(file_id, safe='')
    with _request(file_url + '?fields=id,name,mimeType,size,trashed', token) as response:
        meta = json.load(response)
    if meta.get('trashed'):
        raise ValueError('That Google Drive file is already in Trash.')
    mime = str(meta.get('mimeType') or '').lower()
    name = str(meta.get('name') or '')
    ext = Path(name).suffix.lower()
    if mime not in MIME_SUFFIX or (destination == 'storm' and not mime.startswith('image/')):
        raise ValueError('This destination only accepts screenshots or supported video files.')
    if ext not in ALLOWED_EXTENSIONS or (ext in {'.mp4','.mov','.m4v','.webm'} and destination == 'storm'):
        ext = MIME_SUFFIX[mime]
    if str(meta.get('size') or '').isdigit() and int(meta['size']) > MAX_BYTES:
        raise ValueError('The selected file exceeds the 150 MB upload limit.')
    destination_path = directory / ('selected_drive_file' + ext)
    size = 0
    try:
        with _request(file_url + '?alt=media', token) as response:
            with destination_path.open('wb') as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('The selected file exceeds the 150 MB upload limit.')
                    out.write(chunk)
        if size == 0:
            raise ValueError('The selected Google Drive file is empty.')
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    return destination_path


def move_selected_file_to_trash(file_id: str, token: str):
    if not FILE_ID_PATTERN.fullmatch(file_id):
        raise ValueError('Invalid Google Drive file selection.')
    url = 'https://www.googleapis.com/drive/v3/files/' + quote(file_id, safe='') + '?fields=id,trashed&supportsAllDrives=true'
    with _request(url, token, method='PATCH', body={'trashed': True}) as response:
        changed = json.load(response)
    if not changed.get('trashed'):
        raise ValueError('Google Drive did not confirm the file was moved to Trash.')
    return True
