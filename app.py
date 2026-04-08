from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import tkinter as tk


APP_TITLE = "动森离线助手"
DATABASE_FILENAME = "animal_crossing_offline.db"
DATA_DIRNAME = "data"
IMAGES_DIRNAME = "images"
ALL_CATEGORIES = "全部分类"
ALL_KINDS = "全部类型"
DISPLAY_COLUMNS = (
    ("item_id", "ID", 90, "center"),
    ("english", "English", 280, "w"),
    ("chinese_simplified", "中文简体", 260, "w"),
    ("category_zh", "分类", 170, "w"),
    ("item_kind", "类型", 220, "w"),
    ("item_kind_code", "类型编码", 100, "center"),
)
SECTION_LABELS = {
    "fish": "鱼类图鉴",
    "bugs": "昆虫图鉴",
    "sea": "海洋生物图鉴",
    "villagers": "村民资料",
    "art": "艺术品与赝品",
    "recipes": "DIY 配方",
    "fossils": "化石",
    "events": "活动日历",
}


@dataclass(slots=True)
class ItemRecord:
    item_id: str
    english: str
    chinese_simplified: str
    item_kind_code: str
    item_kind: str
    category_zh: str
    search_blob: str


@dataclass(slots=True)
class LoadedData:
    path: Path
    records: list[ItemRecord]
    category_counts: list[tuple[str, int]]
    kind_counts: list[tuple[str, int]]


def clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.replace("\u2019", "'").replace("\u00a0", " ").lower().split())


def build_lookup_candidates(name: str) -> list[str]:
    raw_name = clean_text(name)
    candidates = [normalize_text(raw_name)]
    if raw_name.endswith(" (DIY recipe)"):
        candidates.append(normalize_text(raw_name[: -len(" (DIY recipe)")]))
    if raw_name.endswith(" (No Variations)"):
        candidates.append(normalize_text(raw_name[: -len(" (No Variations)")]))
    if raw_name.endswith(" (forgery)"):
        candidates.append(normalize_text(raw_name[: -len(" (forgery)")]))
    return list(dict.fromkeys([candidate for candidate in candidates if candidate]))


def build_translation_map(records: list[ItemRecord]) -> dict[str, str]:
    counters: dict[str, dict[str, int]] = {}
    for record in records:
        english_name = clean_text(record.english)
        chinese_name = clean_text(record.chinese_simplified)
        if not english_name or english_name == "(None)" or not chinese_name or chinese_name == "(None)":
            continue
        normalized = normalize_text(english_name)
        counters.setdefault(normalized, {})
        counters[normalized][chinese_name] = counters[normalized].get(chinese_name, 0) + 1

    translations: dict[str, str] = {}
    for normalized, options in counters.items():
        translations[normalized] = sorted(options.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return translations


def safe_int(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, value or "")


def filter_records(records: list[ItemRecord], search_text: str, category_filter: str, kind_filter: str) -> list[ItemRecord]:
    tokens = [token for token in normalize_text(search_text).split(" ") if token]
    filtered: list[ItemRecord] = []
    for record in records:
        if category_filter != ALL_CATEGORIES and record.category_zh != category_filter:
            continue
        if kind_filter != ALL_KINDS and record.item_kind != kind_filter:
            continue
        if tokens and any(token not in record.search_blob for token in tokens):
            continue
        filtered.append(record)
    return filtered


def sort_records(records: list[ItemRecord], column: str, descending: bool) -> list[ItemRecord]:
    numeric_columns = {"item_id", "item_kind_code"}

    def sort_key(record: ItemRecord):
        value = getattr(record, column)
        if column in numeric_columns:
            return safe_int(value)
        normalized = value.lower()
        return (normalized == "", normalized)

    return sorted(records, key=sort_key, reverse=descending)


def load_photo_image(image_path: Path | None, *, max_size: int = 180) -> tk.PhotoImage | None:
    if image_path is None or not image_path.exists():
        return None
    image = tk.PhotoImage(file=str(image_path))
    factor = max(1, math.ceil(max(image.width() / max_size, image.height() / max_size)))
    if factor > 1:
        image = image.subsample(factor, factor)
    return image


def set_text_widget(widget: ScrolledText, text: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", tk.END)
    widget.insert("1.0", text.strip() if text.strip() else "暂无内容")
    widget.configure(state="disabled")


def open_connection(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def load_database_items(db_path: Path) -> LoadedData:
    with open_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT item_id, COALESCE(english, '') AS english, COALESCE(chinese, '') AS chinese,
                   COALESCE(item_kind_code, '') AS item_kind_code, COALESCE(item_kind, '') AS item_kind,
                   COALESCE(category_zh, '') AS category_zh
            FROM items
            ORDER BY item_id
            """
        ).fetchall()

    records = [
        ItemRecord(
            item_id=str(row["item_id"]),
            english=row["english"],
            chinese_simplified=row["chinese"],
            item_kind_code=row["item_kind_code"],
            item_kind=row["item_kind"],
            category_zh=row["category_zh"],
            search_blob=normalize_text(
                " ".join(
                    [
                        str(row["item_id"]),
                        row["english"],
                        row["chinese"],
                        row["item_kind_code"],
                        row["item_kind"],
                        row["category_zh"],
                    ]
                )
            ),
        )
        for row in rows
    ]
    category_counts = Counter(record.category_zh for record in records if record.category_zh).most_common()
    kind_counts = Counter(record.item_kind for record in records if record.item_kind).most_common()
    return LoadedData(path=db_path, records=records, category_counts=category_counts, kind_counts=kind_counts)


def resolve_default_db_path(cli_path: str | None) -> Path | None:
    candidates: list[Path] = []
    if cli_path:
        candidates.append(Path(cli_path).expanduser())

    executable_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    script_dir = Path(__file__).resolve().parent
    candidates.extend(
        [
            executable_dir / DATA_DIRNAME / DATABASE_FILENAME,
            executable_dir / DATABASE_FILENAME,
            script_dir / DATA_DIRNAME / DATABASE_FILENAME,
            script_dir / DATABASE_FILENAME,
            Path.cwd() / DATA_DIRNAME / DATABASE_FILENAME,
            Path.cwd() / DATABASE_FILENAME,
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return candidates[0] if cli_path else None


class KnowledgeBase:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.data_dir = db_path.parent
        self.item_entries: dict[str, dict[str, str]] = {}
        self.item_lookup: dict[str, str] = {}
        self.encyclopedia: dict[str, list[dict[str, str]]] = {}
        self.meta: dict[str, str] = {}
        self.generated_at = ""
        self._load()

    def _load(self) -> None:
        if not self.db_path.exists():
            return

        with open_connection(self.db_path) as connection:
            self.meta = {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM meta")}
            self.generated_at = self.meta.get("generated_at", "")

            for row in connection.execute(
                """
                SELECT entry_id, entry_kind, title, COALESCE(chinese_title, '') AS chinese_title,
                       COALESCE(subtitle, '') AS subtitle, COALESCE(dataset_id, '') AS dataset_id,
                       COALESCE(dataset_label, '') AS dataset_label, COALESCE(section_id, '') AS section_id,
                       COALESCE(section_label, '') AS section_label, COALESCE(page_title, '') AS page_title,
                       COALESCE(wiki_url, '') AS wiki_url, COALESCE(summary, '') AS summary,
                       COALESCE(facts_text, '') AS facts_text, COALESCE(image_rel_path, '') AS image_rel_path
                FROM knowledge_entries
                ORDER BY title COLLATE NOCASE
                """
            ):
                entry = {
                    "id": row["entry_id"],
                    "entry_kind": row["entry_kind"],
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
                    self.item_entries[row["entry_id"]] = entry
                else:
                    self.encyclopedia.setdefault(row["section_id"], []).append(entry)

            self.item_lookup = {
                row["alias"]: row["entry_id"]
                for row in connection.execute("SELECT alias, entry_id FROM knowledge_aliases")
            }

    @property
    def is_available(self) -> bool:
        return bool(self.item_entries or self.encyclopedia)

    def resolve_item(self, english_name: str) -> dict[str, str] | None:
        for candidate in build_lookup_candidates(english_name):
            entry_id = self.item_lookup.get(candidate)
            if entry_id and entry_id in self.item_entries:
                return self.item_entries[entry_id]
        return None

    def resolve_chinese_title(self, title: str, translation_map: dict[str, str]) -> str:
        for candidate in build_lookup_candidates(title):
            translated = translation_map.get(candidate, "")
            if translated:
                return translated
        return ""

    def get_section_entries(self, section_id: str) -> list[dict[str, str]]:
        return list(self.encyclopedia.get(section_id, []))

    def image_path(self, relative_path: str) -> Path | None:
        if not relative_path:
            return None
        path = self.data_dir / relative_path
        if path.exists():
            return path
        return None

    def section_choices(self) -> list[tuple[str, str]]:
        return [(section_id, SECTION_LABELS[section_id]) for section_id in SECTION_LABELS if self.encyclopedia.get(section_id)]

    def summary_stats(self) -> str:
        items_rows = self.meta.get("items_rows", "0")
        entries = self.meta.get("knowledge_entries", "0")
        images = self.meta.get("copied_images", "0")
        generated_at = self.generated_at or "未知时间"
        return f"物品 {items_rows} 条 | 百科 {entries} 条 | 图片 {images} 张 | 生成时间 {generated_at}"


class OfflineAssistantApp:
    def __init__(self, root: tk.Tk, initial_db_path: Path | None):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1520x920")
        self.root.minsize(1180, 760)

        self.data: LoadedData | None = None
        self.knowledge_base: KnowledgeBase | None = None
        self.translation_map: dict[str, str] = {}
        self.visible_records: list[ItemRecord] = []
        self.sort_column = "item_id"
        self.sort_descending = False
        self.filter_job: str | None = None

        self.search_var = tk.StringVar()
        self.category_var = tk.StringVar(value=ALL_CATEGORIES)
        self.kind_var = tk.StringVar(value=ALL_KINDS)
        self.path_var = tk.StringVar(value="尚未加载数据库")
        self.cache_var = tk.StringVar(value="准备就绪")
        self.status_var = tk.StringVar(value="准备就绪")

        self.detail_id_var = tk.StringVar(value="-")
        self.detail_english_var = tk.StringVar(value="-")
        self.detail_chinese_var = tk.StringVar(value="-")
        self.detail_category_var = tk.StringVar(value="-")
        self.detail_kind_var = tk.StringVar(value="-")
        self.detail_source_var = tk.StringVar(value="-")

        self.encyclopedia_title_var = tk.StringVar(value="-")
        self.encyclopedia_chinese_var = tk.StringVar(value="-")
        self.encyclopedia_subtitle_var = tk.StringVar(value="-")
        self.encyclopedia_section_var = tk.StringVar(value="")
        self.encyclopedia_search_var = tk.StringVar()
        self.encyclopedia_section_choices: list[tuple[str, str]] = []
        self.encyclopedia_visible_entries: list[dict[str, str]] = []

        self.item_image_ref: tk.PhotoImage | None = None
        self.encyclopedia_image_ref: tk.PhotoImage | None = None

        self.configure_style()
        self.build_ui()
        self.bind_events()

        if initial_db_path and initial_db_path.exists():
            self.load_database(initial_db_path)
        else:
            self.prompt_for_database()

    def configure_style(self) -> None:
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Header.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Value.TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("LargeValue.TLabel", font=("Microsoft YaHei UI", 12))

    def build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        top_frame = ttk.Frame(self.root, padding=(12, 10))
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.columnconfigure(0, weight=1)

        ttk.Label(top_frame, text="当前数据库", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(top_frame, textvariable=self.path_var, style="Value.TLabel").grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(top_frame, text="内置资料", style="Header.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(top_frame, textvariable=self.cache_var, style="Value.TLabel").grid(row=3, column=0, sticky="ew", pady=(4, 0))

        button_frame = ttk.Frame(top_frame)
        button_frame.grid(row=0, column=1, rowspan=4, sticky="ne", padx=(16, 0))
        ttk.Button(button_frame, text="打开数据库", command=self.choose_database).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_frame, text="刷新数据库", command=self.reload_database).grid(row=0, column=1)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))

        self.items_tab = ttk.Frame(self.notebook)
        self.encyclopedia_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.items_tab, text="物品对照")
        self.notebook.add(self.encyclopedia_tab, text="离线百科")

        self.build_items_tab()
        self.build_encyclopedia_tab()

        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=(10, 6))
        status_bar.grid(row=2, column=0, sticky="ew")

    def build_items_tab(self) -> None:
        self.items_tab.columnconfigure(0, weight=1)
        self.items_tab.rowconfigure(1, weight=1)

        filter_frame = ttk.LabelFrame(self.items_tab, text="搜索与分类", padding=(12, 10))
        filter_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 8))
        filter_frame.columnconfigure(1, weight=1)
        filter_frame.columnconfigure(3, weight=1)
        filter_frame.columnconfigure(5, weight=1)

        ttk.Label(filter_frame, text="关键词").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.search_entry = ttk.Entry(filter_frame, textvariable=self.search_var)
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))

        ttk.Label(filter_frame, text="分类").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.category_combo = ttk.Combobox(filter_frame, textvariable=self.category_var, state="readonly")
        self.category_combo.grid(row=0, column=3, sticky="ew", padx=(0, 16))

        ttk.Label(filter_frame, text="类型").grid(row=0, column=4, sticky="w", padx=(0, 8))
        self.kind_combo = ttk.Combobox(filter_frame, textvariable=self.kind_var, state="readonly")
        self.kind_combo.grid(row=0, column=5, sticky="ew", padx=(0, 12))
        ttk.Button(filter_frame, text="清空筛选", command=self.clear_filters).grid(row=0, column=6)

        paned = ttk.Panedwindow(self.items_tab, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew")

        left_frame = ttk.Frame(paned)
        right_frame = ttk.Frame(paned, padding=(12, 4))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)
        right_frame.columnconfigure(0, weight=1)
        paned.add(left_frame, weight=4)
        paned.add(right_frame, weight=3)

        self.tree = ttk.Treeview(left_frame, columns=[column_id for column_id, _, _, _ in DISPLAY_COLUMNS], show="headings", selectmode="browse")
        for column_id, title, width, anchor in DISPLAY_COLUMNS:
            self.tree.heading(column_id, text=title, command=lambda c=column_id: self.on_heading_click(c))
            self.tree.column(column_id, width=width, minwidth=80, anchor=anchor, stretch=True)
        self.tree.tag_configure("odd", background="#f7f9fb")
        vertical_scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree.yview)
        horizontal_scrollbar = ttk.Scrollbar(left_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical_scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.build_item_detail_panel(right_frame)

    def build_item_detail_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        image_frame = ttk.Frame(parent)
        image_frame.grid(row=0, column=0, sticky="ew")
        image_frame.columnconfigure(1, weight=1)
        self.item_image_label = ttk.Label(image_frame, text="暂无图标", anchor="center", relief="solid", width=22)
        self.item_image_label.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 12))

        ttk.Label(image_frame, text="English", style="Header.TLabel").grid(row=0, column=1, sticky="w")
        english_row = ttk.Frame(image_frame)
        english_row.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        english_row.columnconfigure(0, weight=1)
        ttk.Label(english_row, textvariable=self.detail_english_var, style="LargeValue.TLabel", wraplength=520, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(english_row, text="复制英文", command=self.copy_english).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(image_frame, text="中文简体", style="Header.TLabel").grid(row=2, column=1, sticky="w", pady=(10, 0))
        chinese_row = ttk.Frame(image_frame)
        chinese_row.grid(row=3, column=1, sticky="ew", pady=(4, 0))
        chinese_row.columnconfigure(0, weight=1)
        ttk.Label(chinese_row, textvariable=self.detail_chinese_var, style="LargeValue.TLabel", wraplength=520, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(chinese_row, text="复制中文", command=self.copy_chinese).grid(row=0, column=1, padx=(8, 0))

        meta_frame = ttk.Frame(parent)
        meta_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        meta_frame.columnconfigure(1, weight=1)
        ttk.Label(meta_frame, text="ID", style="Header.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 10))
        ttk.Label(meta_frame, textvariable=self.detail_id_var, style="Value.TLabel").grid(row=0, column=1, sticky="nw")
        ttk.Label(meta_frame, text="分类", style="Header.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        ttk.Label(meta_frame, textvariable=self.detail_category_var, style="Value.TLabel").grid(row=1, column=1, sticky="nw", pady=(6, 0))
        ttk.Label(meta_frame, text="类型", style="Header.TLabel").grid(row=2, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        ttk.Label(meta_frame, textvariable=self.detail_kind_var, style="Value.TLabel").grid(row=2, column=1, sticky="nw", pady=(6, 0))
        ttk.Label(meta_frame, text="百科来源", style="Header.TLabel").grid(row=3, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        ttk.Label(meta_frame, textvariable=self.detail_source_var, style="Value.TLabel", wraplength=520, justify="left").grid(row=3, column=1, sticky="nw", pady=(6, 0))

        ttk.Label(parent, text="Wiki 摘要", style="Header.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 4))
        self.item_summary_text = ScrolledText(parent, height=8, wrap="word")
        self.item_summary_text.grid(row=3, column=0, sticky="nsew")
        self.item_summary_text.configure(state="disabled")

        ttk.Label(parent, text="离线资料详情", style="Header.TLabel").grid(row=4, column=0, sticky="w", pady=(12, 4))
        self.item_facts_text = ScrolledText(parent, height=12, wrap="word")
        self.item_facts_text.grid(row=5, column=0, sticky="nsew")
        self.item_facts_text.configure(state="disabled")
        parent.rowconfigure(5, weight=1)

    def build_encyclopedia_tab(self) -> None:
        self.encyclopedia_tab.columnconfigure(0, weight=1)
        self.encyclopedia_tab.rowconfigure(1, weight=1)

        control_frame = ttk.LabelFrame(self.encyclopedia_tab, text="离线百科搜索", padding=(12, 10))
        control_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=(2, 8))
        control_frame.columnconfigure(3, weight=1)

        ttk.Label(control_frame, text="资料分类").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.encyclopedia_combo = ttk.Combobox(control_frame, textvariable=self.encyclopedia_section_var, state="readonly")
        self.encyclopedia_combo.grid(row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(control_frame, text="关键词").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.encyclopedia_search_entry = ttk.Entry(control_frame, textvariable=self.encyclopedia_search_var)
        self.encyclopedia_search_entry.grid(row=0, column=3, sticky="ew")

        paned = ttk.Panedwindow(self.encyclopedia_tab, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew")
        left_frame = ttk.Frame(paned)
        right_frame = ttk.Frame(paned, padding=(12, 4))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)
        right_frame.columnconfigure(0, weight=1)
        paned.add(left_frame, weight=3)
        paned.add(right_frame, weight=4)

        self.encyclopedia_tree = ttk.Treeview(
            left_frame,
            columns=("title", "chinese", "subtitle"),
            show="headings",
            selectmode="browse",
        )
        self.encyclopedia_tree.heading("title", text="名称")
        self.encyclopedia_tree.heading("chinese", text="中文对照")
        self.encyclopedia_tree.heading("subtitle", text="概要")
        self.encyclopedia_tree.column("title", width=220, anchor="w")
        self.encyclopedia_tree.column("chinese", width=220, anchor="w")
        self.encyclopedia_tree.column("subtitle", width=320, anchor="w")
        e_vscroll = ttk.Scrollbar(left_frame, orient="vertical", command=self.encyclopedia_tree.yview)
        self.encyclopedia_tree.configure(yscrollcommand=e_vscroll.set)
        self.encyclopedia_tree.grid(row=0, column=0, sticky="nsew")
        e_vscroll.grid(row=0, column=1, sticky="ns")

        self.encyclopedia_image_label = ttk.Label(right_frame, text="暂无图标", anchor="center", relief="solid", width=22)
        self.encyclopedia_image_label.grid(row=0, column=0, sticky="nw")
        title_row = ttk.Frame(right_frame)
        title_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        title_row.columnconfigure(0, weight=1)
        ttk.Label(title_row, textvariable=self.encyclopedia_title_var, style="LargeValue.TLabel", wraplength=620, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(title_row, text="复制名称", command=self.copy_encyclopedia_title).grid(row=0, column=1, padx=(8, 0))

        chinese_row = ttk.Frame(right_frame)
        chinese_row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        chinese_row.columnconfigure(0, weight=1)
        ttk.Label(chinese_row, textvariable=self.encyclopedia_chinese_var, style="LargeValue.TLabel", wraplength=620, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(chinese_row, text="复制中文", command=self.copy_encyclopedia_chinese).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(right_frame, textvariable=self.encyclopedia_subtitle_var, style="Value.TLabel", wraplength=620, justify="left").grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Label(right_frame, text="Wiki 摘要", style="Header.TLabel").grid(row=4, column=0, sticky="w", pady=(12, 4))
        self.encyclopedia_summary_text = ScrolledText(right_frame, height=10, wrap="word")
        self.encyclopedia_summary_text.grid(row=5, column=0, sticky="nsew")
        self.encyclopedia_summary_text.configure(state="disabled")

        ttk.Label(right_frame, text="离线资料详情", style="Header.TLabel").grid(row=6, column=0, sticky="w", pady=(12, 4))
        self.encyclopedia_facts_text = ScrolledText(right_frame, height=14, wrap="word")
        self.encyclopedia_facts_text.grid(row=7, column=0, sticky="nsew")
        self.encyclopedia_facts_text.configure(state="disabled")
        right_frame.rowconfigure(7, weight=1)

    def bind_events(self) -> None:
        self.search_var.trace_add("write", lambda *_: self.schedule_filter())
        self.category_combo.bind("<<ComboboxSelected>>", lambda *_: self.apply_filters())
        self.kind_combo.bind("<<ComboboxSelected>>", lambda *_: self.apply_filters())
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_selection)
        self.encyclopedia_combo.bind("<<ComboboxSelected>>", lambda *_: self.refresh_encyclopedia_view())
        self.encyclopedia_search_var.trace_add("write", lambda *_: self.refresh_encyclopedia_view())
        self.encyclopedia_tree.bind("<<TreeviewSelect>>", self.on_encyclopedia_selection)
        self.root.bind("<Control-f>", self.focus_search)
        self.root.bind("<F5>", lambda *_: self.reload_database())

    def focus_search(self, _event=None):
        if self.notebook.index(self.notebook.select()) == 0:
            self.search_entry.focus_set()
            self.search_entry.selection_range(0, tk.END)
        else:
            self.encyclopedia_search_entry.focus_set()
            self.encyclopedia_search_entry.selection_range(0, tk.END)
        return "break"

    def prompt_for_database(self) -> None:
        messagebox.showinfo(APP_TITLE, "没有自动找到数据库，请手动选择 .db 文件。")
        self.choose_database()

    def choose_database(self) -> None:
        file_path = filedialog.askopenfilename(title="选择 SQLite 数据库", filetypes=(("SQLite Database", "*.db"), ("All Files", "*.*")))
        if file_path:
            self.load_database(Path(file_path))

    def reload_database(self) -> None:
        if self.data is None:
            self.choose_database()
            return
        self.load_database(self.data.path)

    def load_database(self, db_path: Path) -> None:
        try:
            loaded = load_database_items(db_path)
            knowledge_base = KnowledgeBase(db_path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"读取数据库失败：\n{exc}")
            return

        self.data = loaded
        self.knowledge_base = knowledge_base
        self.translation_map = build_translation_map(loaded.records)
        self.path_var.set(str(db_path))
        self.cache_var.set(knowledge_base.summary_stats())
        self.category_combo["values"] = [ALL_CATEGORIES] + [name for name, _ in loaded.category_counts]
        self.kind_combo["values"] = [ALL_KINDS] + [name for name, _ in loaded.kind_counts]
        if self.category_var.get() not in self.category_combo["values"]:
            self.category_var.set(ALL_CATEGORIES)
        if self.kind_var.get() not in self.kind_combo["values"]:
            self.kind_var.set(ALL_KINDS)

        self.encyclopedia_section_choices = knowledge_base.section_choices()
        self.encyclopedia_combo["values"] = [label for _, label in self.encyclopedia_section_choices]
        if self.encyclopedia_combo["values"]:
            if self.encyclopedia_section_var.get() not in self.encyclopedia_combo["values"]:
                self.encyclopedia_section_var.set(self.encyclopedia_combo["values"][0])
        else:
            self.encyclopedia_section_var.set("")

        self.apply_filters()
        self.refresh_encyclopedia_view()

    def clear_filters(self) -> None:
        self.search_var.set("")
        self.category_var.set(ALL_CATEGORIES)
        self.kind_var.set(ALL_KINDS)
        self.apply_filters()

    def schedule_filter(self) -> None:
        if self.filter_job is not None:
            self.root.after_cancel(self.filter_job)
        self.filter_job = self.root.after(180, self.apply_filters)

    def apply_filters(self) -> None:
        if self.filter_job is not None:
            self.root.after_cancel(self.filter_job)
            self.filter_job = None
        if self.data is None:
            return

        filtered = filter_records(
            self.data.records,
            search_text=self.search_var.get(),
            category_filter=self.category_var.get() or ALL_CATEGORIES,
            kind_filter=self.kind_var.get() or ALL_KINDS,
        )
        self.visible_records = sort_records(filtered, self.sort_column, self.sort_descending)
        self.render_tree()
        self.status_var.set(f"已加载 {len(self.data.records)} 条物品 | 当前显示 {len(self.visible_records)} 条")

    def render_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, record in enumerate(self.visible_records):
            tags = ("odd",) if index % 2 else ()
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(record.item_id, record.english, record.chinese_simplified, record.category_zh, record.item_kind, record.item_kind_code),
                tags=tags,
            )
        if self.visible_records:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self.tree.see("0")
            self.update_detail_panel(self.visible_records[0])
        else:
            self.clear_detail_panel()

    def clear_detail_panel(self) -> None:
        self.detail_id_var.set("-")
        self.detail_english_var.set("-")
        self.detail_chinese_var.set("-")
        self.detail_category_var.set("-")
        self.detail_kind_var.set("-")
        self.detail_source_var.set("-")
        self.item_image_ref = None
        self.item_image_label.configure(image="", text="暂无图标")
        set_text_widget(self.item_summary_text, "")
        set_text_widget(self.item_facts_text, "")

    def update_detail_panel(self, record: ItemRecord) -> None:
        self.detail_id_var.set(f"{record.item_id} / 类型编码 {record.item_kind_code or '-'}")
        self.detail_english_var.set(record.english or "(空)")
        self.detail_chinese_var.set(record.chinese_simplified or "(空)")
        self.detail_category_var.set(record.category_zh or "(空)")
        self.detail_kind_var.set(record.item_kind or "(空)")

        wiki_entry = self.knowledge_base.resolve_item(record.english) if self.knowledge_base else None
        if wiki_entry is not None:
            chinese_title = clean_text(wiki_entry.get("chinese_title")) or self.knowledge_base.resolve_chinese_title(record.english, self.translation_map)
            source = wiki_entry.get("dataset_label", "离线资料")
            if chinese_title:
                source = f"{source} | 百科中文：{chinese_title}"
            self.detail_source_var.set(source)

            facts = clean_text(wiki_entry.get("facts_text"))
            if wiki_entry.get("wiki_url"):
                facts = facts + ("\n\n" if facts else "") + f"来源页面：{wiki_entry['wiki_url']}"
            set_text_widget(self.item_summary_text, clean_text(wiki_entry.get("summary")))
            set_text_widget(self.item_facts_text, facts)

            image = load_photo_image(self.knowledge_base.image_path(clean_text(wiki_entry.get("image_rel_path"))))
            self.item_image_ref = image
            if image is not None:
                self.item_image_label.configure(image=image, text="")
            else:
                self.item_image_label.configure(image="", text="暂无图标")
        else:
            self.detail_source_var.set("数据库中没有该条目的百科扩展信息")
            set_text_widget(self.item_summary_text, "")
            set_text_widget(self.item_facts_text, "")
            self.item_image_ref = None
            self.item_image_label.configure(image="", text="暂无图标")

    def refresh_current_item_detail(self) -> None:
        selection = self.tree.selection()
        if selection:
            record_index = int(selection[0])
            if 0 <= record_index < len(self.visible_records):
                self.update_detail_panel(self.visible_records[record_index])
                return
        if self.visible_records:
            self.update_detail_panel(self.visible_records[0])
        else:
            self.clear_detail_panel()

    def on_tree_selection(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        record_index = int(selection[0])
        if 0 <= record_index < len(self.visible_records):
            self.update_detail_panel(self.visible_records[record_index])

    def on_heading_click(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False
        self.apply_filters()

    def current_section_id(self) -> str:
        selected_label = self.encyclopedia_section_var.get()
        for section_id, label in self.encyclopedia_section_choices:
            if label == selected_label:
                return section_id
        return self.encyclopedia_section_choices[0][0] if self.encyclopedia_section_choices else ""

    def resolve_encyclopedia_chinese_title(self, entry: dict[str, str]) -> str:
        chinese_title = clean_text(entry.get("chinese_title"))
        if chinese_title:
            return chinese_title
        if self.knowledge_base is not None:
            return self.knowledge_base.resolve_chinese_title(entry.get("title", ""), self.translation_map)
        return ""

    def refresh_encyclopedia_view(self) -> None:
        section_id = self.current_section_id()
        entries = self.knowledge_base.get_section_entries(section_id) if self.knowledge_base and section_id else []
        tokens = [token for token in normalize_text(self.encyclopedia_search_var.get()).split(" ") if token]
        self.encyclopedia_visible_entries = []
        for entry in entries:
            display_entry = dict(entry)
            display_entry["resolved_chinese_title"] = self.resolve_encyclopedia_chinese_title(entry)
            search_blob = normalize_text(
                " ".join(
                    [
                        display_entry.get("title", ""),
                        display_entry.get("resolved_chinese_title", ""),
                        display_entry.get("subtitle", ""),
                        display_entry.get("summary", ""),
                        display_entry.get("facts_text", ""),
                    ]
                )
            )
            if tokens and any(token not in search_blob for token in tokens):
                continue
            self.encyclopedia_visible_entries.append(display_entry)

        self.encyclopedia_tree.delete(*self.encyclopedia_tree.get_children())
        for index, entry in enumerate(self.encyclopedia_visible_entries):
            self.encyclopedia_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(entry.get("title", ""), entry.get("resolved_chinese_title", "") or "-", entry.get("subtitle", "")),
            )
        if self.encyclopedia_visible_entries:
            self.encyclopedia_tree.selection_set("0")
            self.encyclopedia_tree.focus("0")
            self.update_encyclopedia_detail(self.encyclopedia_visible_entries[0])
        else:
            self.clear_encyclopedia_detail()

    def clear_encyclopedia_detail(self) -> None:
        self.encyclopedia_title_var.set("-")
        self.encyclopedia_chinese_var.set("-")
        self.encyclopedia_subtitle_var.set("-")
        self.encyclopedia_image_ref = None
        self.encyclopedia_image_label.configure(image="", text="暂无图标")
        set_text_widget(self.encyclopedia_summary_text, "")
        set_text_widget(self.encyclopedia_facts_text, "")

    def update_encyclopedia_detail(self, entry: dict[str, str]) -> None:
        self.encyclopedia_title_var.set(entry.get("title", "-"))
        self.encyclopedia_chinese_var.set(entry.get("resolved_chinese_title", "") or "暂无中文对照")
        self.encyclopedia_subtitle_var.set(entry.get("subtitle", "-"))
        facts = clean_text(entry.get("facts_text"))
        if entry.get("wiki_url"):
            facts = facts + ("\n\n" if facts else "") + f"来源页面：{entry['wiki_url']}"
        set_text_widget(self.encyclopedia_summary_text, clean_text(entry.get("summary")))
        set_text_widget(self.encyclopedia_facts_text, facts)

        image = load_photo_image(self.knowledge_base.image_path(clean_text(entry.get("image_rel_path")))) if self.knowledge_base else None
        self.encyclopedia_image_ref = image
        if image is not None:
            self.encyclopedia_image_label.configure(image=image, text="")
        else:
            self.encyclopedia_image_label.configure(image="", text="暂无图标")

    def on_encyclopedia_selection(self, _event=None) -> None:
        selection = self.encyclopedia_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self.encyclopedia_visible_entries):
            self.update_encyclopedia_detail(self.encyclopedia_visible_entries[index])

    def copy_to_clipboard(self, value: str, success_message: str) -> None:
        cleaned = clean_text(value)
        if not cleaned or cleaned.startswith("暂无"):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(cleaned)
        self.status_var.set(success_message)

    def copy_english(self) -> None:
        self.copy_to_clipboard(self.detail_english_var.get(), "已复制英文名称")

    def copy_chinese(self) -> None:
        self.copy_to_clipboard(self.detail_chinese_var.get(), "已复制中文名称")

    def copy_encyclopedia_title(self) -> None:
        self.copy_to_clipboard(self.encyclopedia_title_var.get(), "已复制百科名称")

    def copy_encyclopedia_chinese(self) -> None:
        self.copy_to_clipboard(self.encyclopedia_chinese_var.get(), "已复制百科中文对照")


def build_self_test_summary(db_path: Path | None) -> dict[str, object]:
    summary: dict[str, object] = {"database_path": str(db_path) if db_path else "", "exists": bool(db_path and db_path.exists())}
    if db_path and db_path.exists():
        loaded = load_database_items(db_path)
        knowledge_base = KnowledgeBase(db_path)
        summary.update(
            {
                "items_rows": len(loaded.records),
                "category_count": len(loaded.category_counts),
                "kind_count": len(loaded.kind_counts),
                "meta": knowledge_base.meta,
                "section_counts": {section_id: len(entries) for section_id, entries in knowledge_base.encyclopedia.items()},
            }
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="动森离线助手（数据库版）")
    parser.add_argument("--db", help="指定 SQLite 数据库路径")
    parser.add_argument("--self-test", action="store_true", help="运行自检，不打开界面")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    db_path = resolve_default_db_path(args.db)
    if args.self_test:
        print(json.dumps(build_self_test_summary(db_path), ensure_ascii=False, indent=2))
        return 0

    root = tk.Tk()
    app = OfflineAssistantApp(root, db_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
