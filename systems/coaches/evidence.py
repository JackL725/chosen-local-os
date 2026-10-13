"""Vetting evidence files: stored on the workstation under PRIVATE_DATA_DIR, never in
the Ledger, the repo, logs or Slack. The Ledger keeps the relative path, SHA-256 and size.

    $PRIVATE_DATA_DIR/coaches/<coach id>/<item id>/<random hex><.ext>

Files keep a random name on disk; the name the founder uploaded is kept only as
`display_name` (a sensitive column: never in events). The encrypted restic backup of
PRIVATE_DATA_DIR already covers this folder.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from systems.coaches.service import CoachError

# Extension -> media type served back. Anything else is refused.
ALLOWED = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}
# Shown in the browser; everything else downloads.
INLINE = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md"}
CHUNK = 1024 * 1024


def extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        allowed = ", ".join(sorted(e.lstrip(".") for e in ALLOWED))
        raise CoachError(f"That file type is not accepted. Use one of: {allowed}.")
    return ext


@contextmanager
def store(
    private_dir: Path, coach_id: str, item_id: str, filename: str, source: BinaryIO, max_bytes: int
) -> Iterator[dict[str, object]]:
    """Copy an upload into place. Yields the record to insert; removes the file if the
    caller's block raises (so a refused Ledger write leaves no orphan file)."""
    ext = extension(filename)
    rel = Path("coaches") / coach_id / item_id / f"{secrets.token_hex(16)}{ext}"
    dest = private_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := source.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise CoachError("That file is larger than the upload limit.")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise CoachError("That file is empty.")
        tmp.rename(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    try:
        yield {
            "relative_path": rel.as_posix(),
            "sha256": digest.hexdigest(),
            "size_bytes": size,
            "content_type": ALLOWED[ext],
            "display_name": Path(filename).name[:200],
        }
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def resolve(private_dir: Path, relative_path: str) -> Path:
    """The file for a stored path, refusing anything outside the private folder."""
    base = private_dir.resolve()
    path = (base / relative_path).resolve()
    if not path.is_relative_to(base / "coaches") or not path.is_file():
        raise CoachError("That file is not available on this workstation.")
    return path
