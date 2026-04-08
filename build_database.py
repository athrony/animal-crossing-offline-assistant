from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


DATABASE_FILENAME = "animal_crossing_offline.db"
DATA_DIRNAME = "data"
IMAGES_DIRNAME = "images"
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.replace("\u2019", "'").replace("\u00a0", " ").lower().split())


def is_meaningful(value: str | None) -> bool:
    cleaned = clean_text(value)
    return bool(cleaned and cleaned != "(None)")


def nullable(value: object) -> str | None:
    cleaned = clean_text(value)
    if not cleaned or cleaned == "(None)":
        return None
    return cleaned


def decode_with_fallback(raw_bytes: bytes) -> tuple[str, str]:
    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            return raw_bytes.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"无法识别 CSV 编码，最后一次错误：{last_error}")


def detect_delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text else ""
    return "\t" if first_line.count("\t") >= first_line.count(",") else ","


def parse_csv_rows(csv_path: Path) -> tuple[list[dict[str, str]], str, str]:
    raw_bytes = csv_path.read_bytes()
    text, encoding = decode_with_fallback(raw_bytes)
    delimiter = detect_delimiter(text)
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    rows = list(reader)
    return rows, encoding, delimiter


def build_translation_map(rows: list[dict[str, str]]) -> dict[str, str]:
    counters: dict[str, dict[str, int]] = {}
    for row in rows:
        english_name = clean_text(row.get("english"))
        chinese_name = clean_text(row.get("chinese_simplified"))
        if not is_meaningful(english_name) or not is_meaningful(chinese_name):
            continue
        normalized = normalize_text(english_name)
        counters.setdefault(normalized, {})
        counters[normalized][chinese_name] = counters[normalized].get(chinese_name, 0) + 1

    translations: dict[str, str] = {}
    for normalized, options in counters.items():
        translations[normalized] = sorted(options.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return translations


def lookup_candidates(name: str) -> list[str]:
    raw_name = clean_text(name)
    candidates = [normalize_text(raw_name)]
    if raw_name.endswith(" (DIY recipe)"):
        candidates.append(normalize_text(raw_name[: -len(" (DIY recipe)")]))
    if raw_name.endswith(" (No Variations)"):
        candidates.append(normalize_text(raw_name[: -len(" (No Variations)")]))
    if raw_name.endswith(" (forgery)"):
        candidates.append(normalize_text(raw_name[: -len(" (forgery)")]))
    return list(dict.fromkeys([candidate for candidate in candidates if candidate]))


def resolve_chinese_title(name: str, translation_map: dict[str, str]) -> str | None:
    for candidate in lookup_candidates(name):
        chinese_name = translation_map.get(candidate)
        if chinese_name:
            return chinese_name
    return None


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        DROP TABLE IF EXISTS meta;
        DROP TABLE IF EXISTS items;
        DROP TABLE IF EXISTS knowledge_entries;
        DROP TABLE IF EXISTS knowledge_aliases;

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE items (
            item_id INTEGER PRIMARY KEY,
            english TEXT,
            chinese TEXT,
            item_kind_code TEXT,
            item_kind TEXT,
            category_zh TEXT
        );

        CREATE TABLE knowledge_entries (
            entry_id TEXT PRIMARY KEY,
            entry_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            chinese_title TEXT,
            subtitle TEXT,
            dataset_id TEXT,
            dataset_label TEXT,
            section_id TEXT,
            section_label TEXT,
            page_title TEXT,
            wiki_url TEXT,
            summary TEXT,
            facts_text TEXT,
            image_rel_path TEXT
        );

        CREATE TABLE knowledge_aliases (
            alias TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL REFERENCES knowledge_entries(entry_id)
        );

        CREATE INDEX idx_items_english ON items (english);
        CREATE INDEX idx_items_chinese ON items (chinese);
        CREATE INDEX idx_items_category ON items (category_zh);
        CREATE INDEX idx_items_kind ON items (item_kind);
        CREATE INDEX idx_knowledge_kind ON knowledge_entries (entry_kind);
        CREATE INDEX idx_knowledge_section ON knowledge_entries (section_id);
        CREATE INDEX idx_knowledge_title ON knowledge_entries (title);
        CREATE INDEX idx_knowledge_chinese_title ON knowledge_entries (chinese_title);
        """
    )


def insert_meta(connection: sqlite3.Connection, stats: dict[str, str]) -> None:
    connection.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        sorted(stats.items()),
    )


def insert_items(connection: sqlite3.Connection, rows: list[dict[str, str]]) -> int:
    inserted = 0
    for row in rows:
        english_name = nullable(row.get("english"))
        chinese_name = nullable(row.get("chinese_simplified"))
        if english_name is None and chinese_name is None:
            continue
        connection.execute(
            """
            INSERT INTO items (item_id, english, chinese, item_kind_code, item_kind, category_zh)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(clean_text(row.get("item_id")) or 0),
                english_name,
                chinese_name,
                nullable(row.get("item_kind_code")),
                nullable(row.get("item_kind")),
                nullable(row.get("category_zh")),
            ),
        )
        inserted += 1
    return inserted


def insert_knowledge(
    connection: sqlite3.Connection,
    payload: dict[str, object],
    translation_map: dict[str, str],
) -> tuple[int, int]:
    knowledge_entries = dict(payload.get("item_entries", {}))
    encyclopedia = dict(payload.get("encyclopedia", {}))
    item_lookup = dict(payload.get("item_lookup", {}))

    entry_count = 0
    alias_count = 0

    for entry in knowledge_entries.values():
        connection.execute(
            """
            INSERT INTO knowledge_entries (
                entry_id, entry_kind, title, chinese_title, subtitle, dataset_id, dataset_label,
                section_id, section_label, page_title, wiki_url, summary, facts_text, image_rel_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_text(entry.get("id")),
                "item",
                clean_text(entry.get("title")),
                nullable(entry.get("chinese_title")) or resolve_chinese_title(clean_text(entry.get("title")), translation_map),
                nullable(entry.get("subtitle")),
                nullable(entry.get("dataset_id")),
                nullable(entry.get("dataset_label")),
                nullable(entry.get("section_id")),
                nullable(entry.get("section_label")),
                nullable(entry.get("page_title")),
                nullable(entry.get("wiki_url")),
                nullable(entry.get("summary")),
                nullable(entry.get("facts_text")),
                nullable(entry.get("image_rel_path")),
            ),
        )
        entry_count += 1

    for section_entries in encyclopedia.values():
        for entry in section_entries:
            connection.execute(
                """
                INSERT INTO knowledge_entries (
                    entry_id, entry_kind, title, chinese_title, subtitle, dataset_id, dataset_label,
                    section_id, section_label, page_title, wiki_url, summary, facts_text, image_rel_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_text(entry.get("id")),
                    "encyclopedia",
                    clean_text(entry.get("title")),
                    nullable(entry.get("chinese_title")) or resolve_chinese_title(clean_text(entry.get("title")), translation_map),
                    nullable(entry.get("subtitle")),
                    nullable(entry.get("dataset_id")),
                    nullable(entry.get("dataset_label")),
                    nullable(entry.get("section_id")),
                    nullable(entry.get("section_label")),
                    nullable(entry.get("page_title")),
                    nullable(entry.get("wiki_url")),
                    nullable(entry.get("summary")),
                    nullable(entry.get("facts_text")),
                    nullable(entry.get("image_rel_path")),
                ),
            )
            entry_count += 1

    for alias, entry_id in item_lookup.items():
        connection.execute(
            "INSERT INTO knowledge_aliases (alias, entry_id) VALUES (?, ?)",
            (clean_text(alias), clean_text(entry_id)),
        )
        alias_count += 1

    return entry_count, alias_count


def copy_images(source_images_dir: Path | None, target_images_dir: Path) -> int:
    if source_images_dir is None or not source_images_dir.exists():
        if target_images_dir.exists():
            shutil.rmtree(target_images_dir)
        return 0

    if target_images_dir.exists():
        shutil.rmtree(target_images_dir)
    shutil.copytree(source_images_dir, target_images_dir)
    return len(list(target_images_dir.rglob("*.*")))


def build_database(
    *,
    csv_path: Path,
    knowledge_base_path: Path,
    source_images_dir: Path | None,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / DATABASE_FILENAME
    if database_path.exists():
        database_path.unlink()

    csv_rows, csv_encoding, csv_delimiter = parse_csv_rows(csv_path)
    translation_map = build_translation_map(csv_rows)
    payload = json.loads(knowledge_base_path.read_text(encoding="utf-8"))

    connection = sqlite3.connect(database_path)
    try:
        create_schema(connection)
        inserted_items = insert_items(connection, csv_rows)
        inserted_entries, inserted_aliases = insert_knowledge(connection, payload, translation_map)

        target_images_dir = output_dir / IMAGES_DIRNAME
        copied_images = copy_images(source_images_dir, target_images_dir)

        insert_meta(
            connection,
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "csv_path": str(csv_path),
                "csv_encoding": csv_encoding,
                "csv_delimiter": repr(csv_delimiter),
                "csv_rows": str(len(csv_rows)),
                "items_rows": str(inserted_items),
                "knowledge_entries": str(inserted_entries),
                "knowledge_aliases": str(inserted_aliases),
                "copied_images": str(copied_images),
                "source_note": "Generated from items.csv and lightweight Nookipedia cache",
            },
        )
        connection.commit()
    finally:
        connection.close()

    return database_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a simplified SQLite database for the offline assistant")
    parser.add_argument("--csv", default=r"items.csv", help="Path to items.csv")
    parser.add_argument(
        "--knowledge-base",
        default=str(Path("offline_cache") / "knowledge_base.json"),
        help="Path to lightweight knowledge_base.json",
    )
    parser.add_argument(
        "--images-dir",
        default=str(Path("offline_cache") / IMAGES_DIRNAME),
        help="Path to source image cache directory",
    )
    parser.add_argument(
        "--output-dir",
        default=DATA_DIRNAME,
        help="Directory where the SQLite database and copied images will be written",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    knowledge_base_path = Path(args.knowledge_base).expanduser()
    images_dir = Path(args.images_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser()

    database_path = build_database(
        csv_path=csv_path,
        knowledge_base_path=knowledge_base_path,
        source_images_dir=images_dir if images_dir.exists() else None,
        output_dir=output_dir,
    )
    print(database_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
