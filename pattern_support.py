from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen


PATTERN_INDEX_URL = "https://www.vectorcmdr.xyz/ACNH-Pattern-Dump-Index/"
USER_AGENT = "AnimalCrossingOfflineAssistant/1.0"
PATTERN_DIRNAME = "patterns"
PATTERN_DOWNLOAD_DIRNAME = "downloads"
PATTERN_IMPORT_DIRNAME = "imports"
PATTERN_PREVIEW_DIRNAME = "previews"
PATTERN_QR_DIRNAME = "qr"
PATTERN_MIRROR_DIRNAME = "pattern_mirror"


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).lower().split())


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(slots=True)
class PatternEntry:
    id: int
    source_type: str
    site_key: str
    title: str
    creator: str
    pattern_type: str
    tags: str
    source_url: str
    preview_url: str
    qr_url: str
    nhd_url: str
    acnl_url: str
    preview_rel_path: str
    qr_rel_path: str
    nhd_rel_path: str
    acnl_rel_path: str
    is_saved: int
    added_at: str
    updated_at: str


class PatternIndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries: list[dict[str, str]] = []
        self.in_tbody = False
        self.in_row = False
        self.current_td_index = -1
        self.current_entry: dict[str, str] | None = None
        self.text_buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr_map = dict(attrs)
        if tag == "tbody":
            self.in_tbody = True
        elif self.in_tbody and tag == "tr":
            self.in_row = True
            self.current_td_index = -1
            self.current_entry = {
                "preview_url": "",
                "qr_url": "",
                "nhd_url": "",
                "acnl_url": "",
                "pattern_type": "",
                "title": "",
                "creator": "",
                "tags": "",
            }
        elif self.in_row and tag == "td":
            self.current_td_index += 1
            self.text_buffer = []
        elif self.in_row and tag == "img":
            src = attr_map.get("data-src") or attr_map.get("src") or ""
            if self.current_entry is None:
                return
            if self.current_td_index == 0 and src:
                self.current_entry["preview_url"] = src
            elif self.current_td_index == 1 and src:
                self.current_entry["qr_url"] = src
        elif self.in_row and tag == "a":
            href = attr_map.get("href", "")
            if self.current_entry is None or not href:
                return
            lowered = href.lower()
            if lowered.endswith(".nhd"):
                self.current_entry["nhd_url"] = href
            elif lowered.endswith(".acnl"):
                self.current_entry["acnl_url"] = href
            elif lowered.endswith(".qr.png"):
                self.current_entry["qr_url"] = href

    def handle_endtag(self, tag):
        if tag == "tbody":
            self.in_tbody = False
        elif self.in_row and tag == "td" and self.current_entry is not None:
            text_value = clean_text("".join(self.text_buffer))
            if self.current_td_index == 3:
                self.current_entry["pattern_type"] = text_value
            elif self.current_td_index == 4:
                self.current_entry["title"] = text_value
            elif self.current_td_index == 5:
                self.current_entry["creator"] = text_value
            elif self.current_td_index == 6:
                self.current_entry["tags"] = text_value.rstrip(", ")
            self.text_buffer = []
        elif self.in_row and tag == "tr":
            if self.current_entry is not None and self.current_entry.get("nhd_url"):
                self.entries.append(self.current_entry)
            self.in_row = False
            self.current_entry = None
            self.current_td_index = -1
            self.text_buffer = []

    def handle_data(self, data):
        if self.in_row and self.current_td_index >= 0:
            self.text_buffer.append(data)


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=90) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_binary(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=90) as response:
        return response.read()


def ensure_pattern_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS pattern_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            site_key TEXT UNIQUE,
            title TEXT,
            creator TEXT,
            pattern_type TEXT,
            tags TEXT,
            source_url TEXT,
            preview_url TEXT,
            qr_url TEXT,
            nhd_url TEXT,
            acnl_url TEXT,
            preview_rel_path TEXT,
            qr_rel_path TEXT,
            nhd_rel_path TEXT,
            acnl_rel_path TEXT,
            is_saved INTEGER NOT NULL DEFAULT 0,
            added_at TEXT,
            updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pattern_title ON pattern_entries(title);
        CREATE INDEX IF NOT EXISTS idx_pattern_creator ON pattern_entries(creator);
        CREATE INDEX IF NOT EXISTS idx_pattern_saved ON pattern_entries(is_saved);
        CREATE INDEX IF NOT EXISTS idx_pattern_source_type ON pattern_entries(source_type);
        """
    )


class PatternRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.data_dir = db_path.parent
        self.patterns_dir = self.data_dir / PATTERN_DIRNAME
        self.downloads_dir = self.patterns_dir / PATTERN_DOWNLOAD_DIRNAME
        self.imports_dir = self.patterns_dir / PATTERN_IMPORT_DIRNAME
        self.previews_dir = self.patterns_dir / PATTERN_PREVIEW_DIRNAME
        self.qr_dir = self.patterns_dir / PATTERN_QR_DIRNAME
        self.mirror_dir = self.patterns_dir / PATTERN_MIRROR_DIRNAME

        self.patterns_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        self.qr_dir.mkdir(parents=True, exist_ok=True)
        self.mirror_dir.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(self.db_path)
        try:
            ensure_pattern_schema(connection)
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        ensure_pattern_schema(connection)
        return connection

    def _normalize_site_url(self, relative_url: str) -> str:
        return urljoin(PATTERN_INDEX_URL, relative_url.replace("\\", "/"))

    def refresh_site_index(self) -> int:
        html = fetch_text(PATTERN_INDEX_URL)
        parser = PatternIndexParser()
        parser.feed(html)
        entries = parser.entries

        with self._connect() as connection:
            for entry in entries:
                nhd_url = self._normalize_site_url(entry["nhd_url"])
                site_key = nhd_url.rsplit("/", 1)[-1].lower()
                source_url = nhd_url
                preview_url = self._normalize_site_url(entry["preview_url"]) if entry["preview_url"] else ""
                qr_url = self._normalize_site_url(entry["qr_url"]) if entry["qr_url"] else ""
                acnl_url = self._normalize_site_url(entry["acnl_url"]) if entry["acnl_url"] else ""

                connection.execute(
                    """
                    INSERT INTO pattern_entries (
                        source_type, site_key, title, creator, pattern_type, tags, source_url,
                        preview_url, qr_url, nhd_url, acnl_url, added_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(site_key) DO UPDATE SET
                        source_type=excluded.source_type,
                        title=excluded.title,
                        creator=excluded.creator,
                        pattern_type=excluded.pattern_type,
                        tags=excluded.tags,
                        source_url=excluded.source_url,
                        preview_url=excluded.preview_url,
                        qr_url=excluded.qr_url,
                        nhd_url=excluded.nhd_url,
                        acnl_url=excluded.acnl_url,
                        updated_at=excluded.updated_at
                    """,
                    (
                        "site",
                        site_key,
                        clean_text(entry["title"]),
                        clean_text(entry["creator"]),
                        clean_text(entry["pattern_type"]),
                        clean_text(entry["tags"]),
                        source_url,
                        preview_url,
                        qr_url,
                        nhd_url,
                        acnl_url,
                        now_iso(),
                        now_iso(),
                    ),
                )
            connection.commit()
        return len(entries)

    def list_patterns(self, query: str = "", saved_only: bool = False) -> list[PatternEntry]:
        clauses: list[str] = []
        params: list[object] = []
        if saved_only:
            clauses.append("is_saved = 1")
        if query.strip():
            like = f"%{query.strip()}%"
            clauses.append("(title LIKE ? OR creator LIKE ? OR tags LIKE ? OR pattern_type LIKE ?)")
            params.extend([like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM pattern_entries
                {where_sql}
                ORDER BY
                    CASE source_type WHEN 'mirror' THEN 0 WHEN 'local' THEN 1 WHEN 'site' THEN 2 ELSE 3 END,
                    is_saved DESC,
                    title COLLATE NOCASE
                """,
                params,
            ).fetchall()
        return [PatternEntry(**dict(row)) for row in rows]

    def get_pattern(self, entry_id: int) -> PatternEntry:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                raise ValueError(f"Pattern entry not found: {entry_id}")
            return PatternEntry(**dict(row))

    def _relative_path(self, path: Path) -> str:
        return path.relative_to(self.data_dir).as_posix()

    def _download_to(self, url: str, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(fetch_binary(url))
        return self._relative_path(destination)

    def refresh_local_mirror_index(self, mirror_root: Path) -> int:
        files_root = mirror_root / "files"
        if not files_root.exists():
            raise FileNotFoundError(files_root)

        entries: list[dict[str, str]] = []
        with self._connect() as connection:
            connection.execute("DELETE FROM pattern_entries WHERE source_type = 'mirror'")
            connection.commit()

        for category in ("simple", "pro", "pat"):
            category_dir = files_root / category
            if not category_dir.exists():
                continue
            for nh_file in sorted(list(category_dir.glob("*.nhd")) + list(category_dir.glob("*.nhpd"))):
                stem = nh_file.stem
                txt_path = category_dir / f"{stem}.txt"
                png_path = category_dir / f"{stem}.png"
                qr_path = category_dir / f"{stem}.QR.png"
                acnl_path = category_dir / f"{stem}.acnl"

                pattern_type = "Pattern"
                title = stem
                creator = "Unknown"
                tags = ""
                if txt_path.exists():
                    lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if len(lines) > 0:
                        pattern_type = clean_text(lines[0]) or pattern_type
                    if len(lines) > 1:
                        title = clean_text(lines[1]) or title
                    if len(lines) > 2:
                        creator = clean_text(lines[2]) or creator
                    if len(lines) > 3:
                        tags = clean_text(lines[3])

                entries.append(
                    {
                        "site_key": f"{category}::{nh_file.name.lower()}",
                        "title": title,
                        "creator": creator,
                        "pattern_type": pattern_type,
                        "tags": tags,
                        "source_url": self._relative_path(nh_file),
                        "preview_url": "",
                        "qr_url": "",
                        "nhd_url": "",
                        "acnl_url": "",
                        "preview_rel_path": self._relative_path(png_path) if png_path.exists() else "",
                        "qr_rel_path": self._relative_path(qr_path) if qr_path.exists() else "",
                        "nhd_rel_path": self._relative_path(nh_file),
                        "acnl_rel_path": self._relative_path(acnl_path) if acnl_path.exists() else "",
                    }
                )

        with self._connect() as connection:
            for entry in entries:
                connection.execute(
                    """
                    INSERT INTO pattern_entries (
                        source_type, site_key, title, creator, pattern_type, tags, source_url,
                        preview_url, qr_url, nhd_url, acnl_url,
                        preview_rel_path, qr_rel_path, nhd_rel_path, acnl_rel_path,
                        is_saved, added_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(site_key) DO UPDATE SET
                        source_type=excluded.source_type,
                        title=excluded.title,
                        creator=excluded.creator,
                        pattern_type=excluded.pattern_type,
                        tags=excluded.tags,
                        source_url=excluded.source_url,
                        nhd_url=excluded.nhd_url,
                        acnl_url=excluded.acnl_url,
                        preview_rel_path=excluded.preview_rel_path,
                        qr_rel_path=excluded.qr_rel_path,
                        nhd_rel_path=excluded.nhd_rel_path,
                        acnl_rel_path=excluded.acnl_rel_path,
                        is_saved=excluded.is_saved,
                        updated_at=excluded.updated_at
                    """,
                    (
                        "mirror",
                        entry["site_key"],
                        entry["title"],
                        entry["creator"],
                        entry["pattern_type"],
                        entry["tags"],
                        entry["source_url"],
                        "",
                        "",
                        entry["nhd_url"],
                        entry["acnl_url"],
                        entry["preview_rel_path"],
                        entry["qr_rel_path"],
                        entry["nhd_rel_path"],
                        entry["acnl_rel_path"],
                        1,
                        now_iso(),
                        now_iso(),
                    ),
                )
            connection.commit()
        return len(entries)

    def ensure_preview_cached(self, entry_id: int) -> PatternEntry:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                raise ValueError(f"Pattern entry not found: {entry_id}")
            entry = PatternEntry(**dict(row))

            updated_preview = entry.preview_rel_path
            updated_qr = entry.qr_rel_path

            if not updated_preview and entry.preview_url:
                preview_name = f"{entry.id}_{Path(entry.preview_url).name}"
                updated_preview = self._download_to(entry.preview_url, self.previews_dir / preview_name)

            if not updated_qr and entry.qr_url:
                qr_name = f"{entry.id}_{Path(entry.qr_url).name}"
                updated_qr = self._download_to(entry.qr_url, self.qr_dir / qr_name)

            if updated_preview != entry.preview_rel_path or updated_qr != entry.qr_rel_path:
                connection.execute(
                    """
                    UPDATE pattern_entries
                    SET preview_rel_path = ?, qr_rel_path = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (updated_preview, updated_qr, now_iso(), entry.id),
                )
                connection.commit()

            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            return PatternEntry(**dict(row))

    def download_pattern(self, entry_id: int) -> PatternEntry:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                raise ValueError(f"Pattern entry not found: {entry_id}")
            entry = PatternEntry(**dict(row))

            nhd_rel_path = entry.nhd_rel_path
            acnl_rel_path = entry.acnl_rel_path
            preview_rel_path = entry.preview_rel_path
            qr_rel_path = entry.qr_rel_path

            if nhd_rel_path and not (self.data_dir / nhd_rel_path).exists():
                nhd_rel_path = ""
            if acnl_rel_path and not (self.data_dir / acnl_rel_path).exists():
                acnl_rel_path = ""
            if preview_rel_path and not (self.data_dir / preview_rel_path).exists():
                preview_rel_path = ""
            if qr_rel_path and not (self.data_dir / qr_rel_path).exists():
                qr_rel_path = ""

            if not nhd_rel_path and entry.nhd_url:
                nhd_rel_path = self._download_to(entry.nhd_url, self.downloads_dir / Path(entry.nhd_url).name)
            if not acnl_rel_path and entry.acnl_url:
                acnl_rel_path = self._download_to(entry.acnl_url, self.downloads_dir / Path(entry.acnl_url).name)
            if entry.preview_url and not preview_rel_path:
                preview_rel_path = self._download_to(entry.preview_url, self.previews_dir / f"{entry.id}_{Path(entry.preview_url).name}")
            if entry.qr_url and not qr_rel_path:
                qr_rel_path = self._download_to(entry.qr_url, self.qr_dir / f"{entry.id}_{Path(entry.qr_url).name}")

            if not nhd_rel_path:
                raise ValueError("This pattern does not provide an NHD/NHPD file.")

            connection.execute(
                """
                UPDATE pattern_entries
                SET nhd_rel_path = ?, acnl_rel_path = ?, preview_rel_path = ?, qr_rel_path = ?,
                    is_saved = 1, updated_at = ?
                WHERE id = ?
                """,
                (nhd_rel_path, acnl_rel_path, preview_rel_path, qr_rel_path, now_iso(), entry.id),
            )
            connection.commit()

            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            return PatternEntry(**dict(row))

    def download_pattern_acnl(self, entry_id: int) -> PatternEntry:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                raise ValueError(f"Pattern entry not found: {entry_id}")
            entry = PatternEntry(**dict(row))

            if entry.acnl_rel_path:
                source_path = self.data_dir / entry.acnl_rel_path
                if source_path.exists():
                    target_path = self.downloads_dir / source_path.name
                    if source_path.resolve() != target_path.resolve():
                        shutil.copy2(source_path, target_path)
                    acnl_rel_path = self._relative_path(target_path)
                else:
                    acnl_rel_path = ""
            elif entry.acnl_url:
                acnl_rel_path = self._download_to(entry.acnl_url, self.downloads_dir / Path(entry.acnl_url).name)
            else:
                raise ValueError("This pattern does not provide an ACNL file.")

            connection.execute(
                """
                UPDATE pattern_entries
                SET acnl_rel_path = ?, is_saved = 1, updated_at = ?
                WHERE id = ?
                """,
                (acnl_rel_path, now_iso(), entry.id),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM pattern_entries WHERE id = ?", (entry_id,)).fetchone()
            return PatternEntry(**dict(row))

    def prepare_export_file(self, entry_id: int, preferred_format: str = "acnl") -> tuple[PatternEntry, Path, str]:
        preferred = preferred_format.lower()
        entry = self.get_pattern(entry_id)

        if preferred == "acnl":
            if entry.acnl_rel_path or entry.acnl_url:
                entry = self.download_pattern_acnl(entry_id)
                source_path = self.data_dir / entry.acnl_rel_path
                return entry, source_path, ".acnl"
            entry = self.download_pattern(entry_id)
            source_path = self.data_dir / entry.nhd_rel_path
            suffix = Path(source_path).suffix.lower() or ".nhd"
            return entry, source_path, suffix

        entry = self.download_pattern(entry_id)
        source_path = self.data_dir / entry.nhd_rel_path
        suffix = Path(source_path).suffix.lower() or ".nhd"
        return entry, source_path, suffix

    def prepare_qr_file(self, entry_id: int) -> tuple[PatternEntry, Path, str]:
        entry = self.ensure_preview_cached(entry_id)
        qr_path = self.image_path(entry.qr_rel_path)
        if qr_path is not None:
            return entry, qr_path, ".png"

        preview_path = self.image_path(entry.preview_rel_path)
        if preview_path is not None:
            return entry, preview_path, ".png"

        raise ValueError("This pattern does not provide a QR or PNG image.")

    def import_pattern_files(self, file_paths: Iterable[Path]) -> int:
        inserted = 0
        with self._connect() as connection:
            for file_path in file_paths:
                if not file_path.exists():
                    continue
                target_path = self.imports_dir / file_path.name
                if file_path.resolve() != target_path.resolve():
                    shutil.copy2(file_path, target_path)

                lowered = file_path.suffix.lower()
                nhd_rel_path = self._relative_path(target_path) if lowered == ".nhd" else ""
                acnl_rel_path = self._relative_path(target_path) if lowered == ".acnl" else ""
                preview_rel_path = self._relative_path(target_path) if lowered == ".png" else ""
                qr_rel_path = self._relative_path(target_path) if lowered == ".png" and file_path.name.lower().endswith(".qr.png") else ""

                connection.execute(
                    """
                    INSERT INTO pattern_entries (
                        source_type, site_key, title, creator, pattern_type, tags, source_url,
                        preview_url, qr_url, nhd_url, acnl_url,
                        preview_rel_path, qr_rel_path, nhd_rel_path, acnl_rel_path,
                        is_saved, added_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "local",
                        f"local::{file_path.name.lower()}::{int(file_path.stat().st_size)}",
                        file_path.stem.replace(".QR", ""),
                        "Local",
                        "Imported",
                        file_path.suffix.lower().lstrip("."),
                        str(file_path),
                        "",
                        "",
                        "",
                        "",
                        preview_rel_path,
                        qr_rel_path,
                        nhd_rel_path,
                        acnl_rel_path,
                        1,
                        now_iso(),
                        now_iso(),
                    ),
                )
                inserted += 1
            connection.commit()
        return inserted

    def image_path(self, relative_path: str) -> Path | None:
        if not relative_path:
            return None
        path = self.data_dir / relative_path
        if path.exists():
            return path
        return None
