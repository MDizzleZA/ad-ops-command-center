"""Media download helpers: thumbnails and reference-ad images stored under data/media."""
import hashlib
from pathlib import Path

import requests

from app.config import MEDIA_DIR


def _download(url: str, dest_dir: Path, name_hint: str) -> str | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    ext = '.jpg'
    ctype = resp.headers.get('content-type', '')
    if 'png' in ctype:
        ext = '.png'
    elif 'gif' in ctype:
        ext = '.gif'
    elif 'webp' in ctype:
        ext = '.webp'
    digest = hashlib.sha1(resp.content).hexdigest()[:10]
    safe = ''.join(ch for ch in name_hint if ch.isalnum() or ch in '-_')[:60]
    path = dest_dir / f'{safe}-{digest}{ext}'
    if not path.exists():
        path.write_bytes(resp.content)
    return str(path)


def download_thumbnail(url: str, name_hint: str) -> str | None:
    return _download(url, MEDIA_DIR / 'thumbnails', name_hint)


def download_reference(url: str, name_hint: str) -> str | None:
    return _download(url, MEDIA_DIR / 'reference', name_hint)


def media_url(local_path: str | None) -> str | None:
    """Convert an absolute path under data/media to the /media URL the app serves."""
    if not local_path:
        return None
    try:
        rel = Path(local_path).resolve().relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return '/media/' + str(rel).replace('\\', '/')
