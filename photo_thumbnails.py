from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - installed in runtime
    Image = None
    ImageOps = None


THUMBNAIL_MAX_SIZE = (480, 480)
THUMBNAIL_COLUMNS = {
    "thumbnail_data": "BYTEA",
    "thumbnail_content_type": "TEXT",
    "thumbnail_width": "INTEGER",
    "thumbnail_height": "INTEGER",
    "thumbnail_source_mtime": "TEXT",
    "thumbnail_generated_at": "TIMESTAMP",
}


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _get_columns(conn, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def ensure_photo_thumbnail_columns(conn) -> set[str]:
    columns = _get_columns(conn, "photos")
    if not columns:
        return columns

    for column_name, column_type in THUMBNAIL_COLUMNS.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {column_name} {column_type}")
            columns.add(column_name)
    return columns


def thumbnail_support_enabled() -> bool:
    return Image is not None and ImageOps is not None


def build_thumbnail_payload(file_path: str | Path):
    if not thumbnail_support_enabled():
        return None

    target_path = Path(file_path)
    try:
        with Image.open(target_path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
            has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
            output = BytesIO()
            if has_alpha:
                image.convert("RGBA").save(output, format="PNG", optimize=True)
                content_type = "image/png"
            else:
                image.convert("RGB").save(output, format="JPEG", quality=82, optimize=True)
                content_type = "image/jpeg"
            return {
                "thumbnail_data": output.getvalue(),
                "thumbnail_content_type": content_type,
                "thumbnail_width": image.width,
                "thumbnail_height": image.height,
                "thumbnail_generated_at": datetime.now().isoformat(timespec="seconds"),
            }
    except Exception:
        return None


def clear_thumbnail(conn, rel_path: str):
    conn.execute(
        """
        UPDATE photos
        SET thumbnail_data = NULL,
            thumbnail_content_type = NULL,
            thumbnail_width = NULL,
            thumbnail_height = NULL,
            thumbnail_source_mtime = NULL,
            thumbnail_generated_at = NULL
        WHERE rel_path = ?
        """,
        (rel_path,),
    )


def persist_thumbnail(conn, rel_path: str, payload: dict, file_mtime: str | None):
    conn.execute(
        """
        UPDATE photos
        SET thumbnail_data = ?,
            thumbnail_content_type = ?,
            thumbnail_width = ?,
            thumbnail_height = ?,
            thumbnail_source_mtime = ?,
            thumbnail_generated_at = ?
        WHERE rel_path = ?
        """,
        (
            payload["thumbnail_data"],
            payload["thumbnail_content_type"],
            payload["thumbnail_width"],
            payload["thumbnail_height"],
            file_mtime,
            payload["thumbnail_generated_at"],
            rel_path,
        ),
    )
