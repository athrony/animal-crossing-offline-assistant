from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import struct
from datetime import datetime
from pathlib import Path


DATABASE_FILENAME = "animal_crossing_offline.db"
DATA_DIRNAME = "data"
IMAGES_DIRNAME = "images"
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16")
NHSE_DEFAULT_DIR = Path.home() / "Documents" / ".tmp_nhse"


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


def read_text_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    return [line.rstrip("\r") for line in text.splitlines()]


def parse_tabbed_name_map(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in read_text_lines(path):
        if not line or "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        mapping[clean_text(key)] = clean_text(value)
    return mapping


def parse_item_menu_icon_names(enum_path: Path) -> list[str]:
    names: list[str] = []
    inside_enum = False
    for raw_line in enum_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("public enum ItemMenuIconType"):
            inside_enum = True
            continue
        if not inside_enum:
            continue
        if line.startswith("}"):
            break
        if not line or line.startswith("///"):
            continue
        token = line.split("=", 1)[0].rstrip(",").strip()
        if token:
            names.append(token)
    return names


def parse_item_menu_icon_indices(bin_path: Path) -> list[int]:
    raw = bin_path.read_bytes()
    count = len(raw) // 2
    return list(struct.unpack(f"<{count}H", raw[: count * 2]))


def build_nhse_item_resource_index(nhse_root: Path) -> tuple[list[str], list[str], dict[int, str]]:
    item_en = read_text_lines(nhse_root / "NHSE.Core" / "Resources" / "text" / "en" / "text_item_en.txt")
    item_zhs = read_text_lines(nhse_root / "NHSE.Core" / "Resources" / "text" / "zhs" / "text_item_zhs.txt")
    icon_names = parse_item_menu_icon_names(nhse_root / "NHSE.Core" / "Structures" / "Item" / "ItemMenuIconType.cs")
    icon_indices = parse_item_menu_icon_indices(nhse_root / "NHSE.Core" / "Resources" / "byte" / "item_menuicon.bin")
    menu_icon_dir = nhse_root / "NHSE.Sprites" / "Resources" / "MenuIcon"

    item_icon_map: dict[int, str] = {}
    for item_id, icon_index in enumerate(icon_indices):
        if 0 <= icon_index < len(icon_names):
            filename = f"{icon_names[icon_index]}.png"
            if (menu_icon_dir / filename).exists():
                item_icon_map[item_id] = filename
    return item_en, item_zhs, item_icon_map


def build_nhse_villager_resource_index(nhse_root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    villager_en = parse_tabbed_name_map(nhse_root / "NHSE.Core" / "Resources" / "text" / "en" / "text_villager_en.txt")
    villager_zhs = parse_tabbed_name_map(nhse_root / "NHSE.Core" / "Resources" / "text" / "zhs" / "text_villager_zhs.txt")
    villager_dir = nhse_root / "NHSE.Sprites" / "Resources" / "Villagers"

    english_to_internal: dict[str, str] = {}
    for internal_name, english_name in villager_en.items():
        english_to_internal[normalize_text(english_name)] = internal_name

    internal_to_image: dict[str, str] = {}
    for png_path in villager_dir.glob("*.png"):
        internal_to_image[png_path.stem] = png_path.name

    return villager_en, villager_zhs, internal_to_image


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
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;

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
            category_zh TEXT,
            image_rel_path TEXT
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


def insert_items(
    connection: sqlite3.Connection,
    rows: list[dict[str, str]],
    nhse_item_en: list[str],
    nhse_item_zhs: list[str],
    item_icon_map: dict[int, str],
    existing_item_image_map: dict[int, str],
) -> tuple[int, dict[str, dict[str, str]]]:
    inserted = 0
    item_name_index: dict[str, dict[str, str]] = {}
    for row in rows:
        item_id = int(clean_text(row.get("item_id")) or 0)
        english_name = nullable(row.get("english"))
        chinese_name = nullable(row.get("chinese_simplified"))
        if item_id < len(nhse_item_en):
            nhse_english = nullable(nhse_item_en[item_id])
            if nhse_english:
                english_name = nhse_english
        if item_id < len(nhse_item_zhs):
            nhse_chinese = nullable(nhse_item_zhs[item_id])
            if nhse_chinese:
                chinese_name = nhse_chinese
        if english_name is None and chinese_name is None:
            continue
        image_rel_path = None
        if item_id in item_icon_map:
            image_rel_path = f"{IMAGES_DIRNAME}/nhse_menu/{item_icon_map[item_id]}"
        elif item_id in existing_item_image_map:
            image_rel_path = existing_item_image_map[item_id]
        connection.execute(
            """
            INSERT INTO items (item_id, english, chinese, item_kind_code, item_kind, category_zh, image_rel_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                english_name,
                chinese_name,
                nullable(row.get("item_kind_code")),
                nullable(row.get("item_kind")),
                nullable(row.get("category_zh")),
                image_rel_path,
            ),
        )
        if english_name:
            normalized = normalize_text(english_name)
            if normalized and normalized not in item_name_index:
                item_name_index[normalized] = {
                    "item_id": str(item_id),
                    "english": english_name,
                    "chinese": chinese_name or "",
                    "image_rel_path": image_rel_path or "",
                }
        inserted += 1
    return inserted, item_name_index


def insert_knowledge(
    connection: sqlite3.Connection,
    payload: dict[str, object],
    translation_map: dict[str, str],
    item_name_index: dict[str, dict[str, str]],
    villager_english_to_internal: dict[str, str],
    villager_zhs: dict[str, str],
    villager_internal_to_image: dict[str, str],
) -> tuple[int, int]:
    knowledge_entries = dict(payload.get("item_entries", {}))
    encyclopedia = dict(payload.get("encyclopedia", {}))
    item_lookup = dict(payload.get("item_lookup", {}))

    entry_count = 0
    alias_count = 0

    def enrich_entry(entry: dict[str, object]) -> tuple[str | None, str | None]:
        title = clean_text(entry.get("title"))
        chinese_title = nullable(entry.get("chinese_title")) or resolve_chinese_title(title, translation_map)
        image_rel_path = nullable(entry.get("image_rel_path"))

        normalized_title = normalize_text(title)
        if normalized_title in item_name_index:
            item_info = item_name_index[normalized_title]
            if not chinese_title and item_info.get("chinese"):
                chinese_title = item_info["chinese"]
            if not image_rel_path and item_info.get("image_rel_path"):
                image_rel_path = item_info["image_rel_path"]

        internal_name = villager_english_to_internal.get(normalized_title)
        if internal_name:
            if not chinese_title:
                chinese_title = villager_zhs.get(internal_name)
            if not image_rel_path and internal_name in villager_internal_to_image:
                image_rel_path = f"{IMAGES_DIRNAME}/nhse_villagers/{villager_internal_to_image[internal_name]}"

        return chinese_title, image_rel_path

    for entry in knowledge_entries.values():
        chinese_title, image_rel_path = enrich_entry(entry)
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
                chinese_title,
                nullable(entry.get("subtitle")),
                nullable(entry.get("dataset_id")),
                nullable(entry.get("dataset_label")),
                nullable(entry.get("section_id")),
                nullable(entry.get("section_label")),
                nullable(entry.get("page_title")),
                nullable(entry.get("wiki_url")),
                nullable(entry.get("summary")),
                nullable(entry.get("facts_text")),
                image_rel_path,
            ),
        )
        entry_count += 1

    for section_entries in encyclopedia.values():
        for entry in section_entries:
            chinese_title, image_rel_path = enrich_entry(entry)
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
                    chinese_title,
                    nullable(entry.get("subtitle")),
                    nullable(entry.get("dataset_id")),
                    nullable(entry.get("dataset_label")),
                    nullable(entry.get("section_id")),
                    nullable(entry.get("section_label")),
                    nullable(entry.get("page_title")),
                    nullable(entry.get("wiki_url")),
                    nullable(entry.get("summary")),
                    nullable(entry.get("facts_text")),
                    image_rel_path,
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
            return len(list(target_images_dir.rglob("*.*")))
        return 0

    if target_images_dir.exists():
        shutil.rmtree(target_images_dir)
    shutil.copytree(source_images_dir, target_images_dir)
    return len(list(target_images_dir.rglob("*.*")))


def copy_directory(source_dir: Path, target_dir: Path) -> int:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)
    return len(list(target_dir.rglob("*.*")))


def load_payload_from_existing_db(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {"item_entries": {}, "item_lookup": {}, "encyclopedia": {}}

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        item_entries: dict[str, dict[str, str]] = {}
        encyclopedia: dict[str, list[dict[str, str]]] = {}

        for row in connection.execute(
            """
            SELECT entry_id, entry_kind, title, COALESCE(chinese_title, '') AS chinese_title,
                   COALESCE(subtitle, '') AS subtitle, COALESCE(dataset_id, '') AS dataset_id,
                   COALESCE(dataset_label, '') AS dataset_label, COALESCE(section_id, '') AS section_id,
                   COALESCE(section_label, '') AS section_label, COALESCE(page_title, '') AS page_title,
                   COALESCE(wiki_url, '') AS wiki_url, COALESCE(summary, '') AS summary,
                   COALESCE(facts_text, '') AS facts_text, COALESCE(image_rel_path, '') AS image_rel_path
            FROM knowledge_entries
            """
        ):
            entry = {
                "id": row["entry_id"],
                "title": row["title"],
                "chinese_title": row["chinese_title"],
                "subtitle": row["subtitle"],
                "dataset_id": row["dataset_id"],
                "dataset_label": row["dataset_label"],
                "section_id": row["section_id"],
                "section_label": row["section_label"],
                "page_title": row["page_title"],
                "wiki_url": row["wiki_url"],
                "summary": row["summary"],
                "facts_text": row["facts_text"],
                "image_rel_path": row["image_rel_path"],
            }
            if row["entry_kind"] == "item":
                item_entries[row["entry_id"]] = entry
            else:
                encyclopedia.setdefault(row["section_id"], []).append(entry)

        item_lookup = {
            row["alias"]: row["entry_id"]
            for row in connection.execute("SELECT alias, entry_id FROM knowledge_aliases")
        }
        return {"item_entries": item_entries, "item_lookup": item_lookup, "encyclopedia": encyclopedia}
    finally:
        connection.close()


def load_item_image_map_from_existing_db(db_path: Path) -> dict[int, str]:
    if not db_path.exists():
        return {}

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
        if "image_rel_path" not in columns:
            return {}
        return {
            int(row["item_id"]): row["image_rel_path"]
            for row in connection.execute(
                """
                SELECT item_id, COALESCE(image_rel_path, '') AS image_rel_path
                FROM items
                WHERE image_rel_path IS NOT NULL AND TRIM(image_rel_path) <> ''
                """
            )
        }
    finally:
        connection.close()


def build_database(
    *,
    csv_path: Path,
    knowledge_base_path: Path,
    source_images_dir: Path | None,
    nhse_root: Path | None,
    seed_db_path: Path | None,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / DATABASE_FILENAME
    payload_seed_path = seed_db_path if seed_db_path is not None and seed_db_path.exists() else database_path
    existing_payload = load_payload_from_existing_db(payload_seed_path)
    existing_item_image_map = load_item_image_map_from_existing_db(payload_seed_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(database_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    if database_path.exists():
        database_path.unlink()

    csv_rows, csv_encoding, csv_delimiter = parse_csv_rows(csv_path)
    translation_map = build_translation_map(csv_rows)
    if knowledge_base_path.exists():
        payload = json.loads(knowledge_base_path.read_text(encoding="utf-8"))
    else:
        payload = existing_payload
    nhse_item_en: list[str] = []
    nhse_item_zhs: list[str] = []
    item_icon_map: dict[int, str] = {}
    villager_english_to_internal: dict[str, str] = {}
    villager_zhs: dict[str, str] = {}
    villager_internal_to_image: dict[str, str] = {}

    if nhse_root is not None and nhse_root.exists():
        nhse_item_en, nhse_item_zhs, item_icon_map = build_nhse_item_resource_index(nhse_root)
        villager_en, villager_zhs, villager_internal_to_image = build_nhse_villager_resource_index(nhse_root)
        villager_english_to_internal = {normalize_text(name): internal for internal, name in villager_en.items()}

    connection = sqlite3.connect(database_path)
    try:
        create_schema(connection)
        inserted_items, item_name_index = insert_items(
            connection,
            csv_rows,
            nhse_item_en,
            nhse_item_zhs,
            item_icon_map,
            existing_item_image_map,
        )
        inserted_entries, inserted_aliases = insert_knowledge(
            connection,
            payload,
            translation_map,
            item_name_index,
            villager_english_to_internal,
            villager_zhs,
            villager_internal_to_image,
        )

        target_images_dir = output_dir / IMAGES_DIRNAME
        copied_images = copy_images(source_images_dir, target_images_dir)
        nhse_menu_icon_count = 0
        nhse_villager_icon_count = 0
        if nhse_root is not None and nhse_root.exists():
            menu_source = nhse_root / "NHSE.Sprites" / "Resources" / "MenuIcon"
            villager_source = nhse_root / "NHSE.Sprites" / "Resources" / "Villagers"
            if menu_source.exists():
                nhse_menu_icon_count = copy_directory(menu_source, target_images_dir / "nhse_menu")
            if villager_source.exists():
                nhse_villager_icon_count = copy_directory(villager_source, target_images_dir / "nhse_villagers")

        insert_meta(
            connection,
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "csv_path": csv_path.name,
                "csv_encoding": csv_encoding,
                "csv_delimiter": repr(csv_delimiter),
                "csv_rows": str(len(csv_rows)),
                "items_rows": str(inserted_items),
                "knowledge_entries": str(inserted_entries),
                "knowledge_aliases": str(inserted_aliases),
                "copied_images": str(copied_images),
                "nhse_menu_icons": str(nhse_menu_icon_count),
                "nhse_villager_icons": str(nhse_villager_icon_count),
                "source_note": "Generated from items.csv, lightweight Nookipedia cache, and NHSE assets",
            },
        )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()

    return database_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a simplified SQLite database for the offline assistant")
    parser.add_argument("--csv", default=str(Path.home() / "Documents" / "items.csv"), help="Path to items.csv")
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
        "--nhse-root",
        default=str(NHSE_DEFAULT_DIR),
        help="Path to a local NHSE repository clone",
    )
    parser.add_argument(
        "--output-dir",
        default=DATA_DIRNAME,
        help="Directory where the SQLite database and copied images will be written",
    )
    parser.add_argument(
        "--seed-db",
        help="Optional existing database used as a source for legacy knowledge entries",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    knowledge_base_path = Path(args.knowledge_base).expanduser()
    images_dir = Path(args.images_dir).expanduser()
    nhse_root = Path(args.nhse_root).expanduser()
    seed_db_path = Path(args.seed_db).expanduser() if args.seed_db else None
    output_dir = Path(args.output_dir).expanduser()

    database_path = build_database(
        csv_path=csv_path,
        knowledge_base_path=knowledge_base_path,
        source_images_dir=images_dir if images_dir.exists() else None,
        nhse_root=nhse_root if nhse_root.exists() else None,
        seed_db_path=seed_db_path if seed_db_path and seed_db_path.exists() else None,
        output_dir=output_dir,
    )
    print(database_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
