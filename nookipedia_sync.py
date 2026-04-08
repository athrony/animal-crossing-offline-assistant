from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


BASE_API_URL = "https://nookipedia.com/w/api.php"
BASE_WIKI_URL = "https://nookipedia.com/wiki/"
USER_AGENT = "ItemsBilingualViewer/2.0 (offline cache builder)"
CACHE_FILE_NAME = "knowledge_base.json"
IMAGES_DIR_NAME = "images"
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".gif", ".ppm", ".pgm"}

ProgressCallback = Callable[[str], None]

ITEM_DATASET_LABELS = {
    "other_items": "材料与杂项",
    "recipes": "DIY 配方",
    "art": "艺术品",
    "clothing": "服装",
    "furniture": "家具",
    "interior": "墙纸/地板/地毯",
    "tools": "工具",
    "photos": "照片与海报",
    "gyroids": "陀螺仪",
    "fish": "鱼类",
    "bugs": "昆虫",
    "sea": "海洋生物",
    "fossils": "化石",
}

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


def emit(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = value.replace("\u2019", "'").replace("\u00a0", " ")
    normalized = re.sub(r"\s+", " ", normalized.strip().lower())
    return normalized


def clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return html.unescape(str(value)).strip()


def format_wiki_url(page_title: str) -> str:
    return BASE_WIKI_URL + quote(page_title.replace(" ", "_"), safe="()':")


def guess_image_suffix(url: str) -> str | None:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        return suffix
    return None


def format_bells(value: str | None) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    if cleaned.isdigit():
        return f"{cleaned} Bells"
    return cleaned


def build_multiline_facts(lines: list[tuple[str, str | None]]) -> str:
    rendered: list[str] = []
    for label, value in lines:
        cleaned = clean_text(value)
        if cleaned:
            rendered.append(f"{label}：{cleaned}")
    return "\n".join(rendered)


def parse_tabular_csv(csv_path: Path) -> list[dict[str, str]]:
    raw = csv_path.read_bytes()
    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            text = raw.decode(encoding)
            delimiter = "\t" if "\t" in text.splitlines()[0] else ","
            reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
            return list(reader)
        except Exception as exc:  # pragma: no cover
            last_error = exc
    raise ValueError(f"无法读取 CSV：{csv_path}，最后一次错误：{last_error}")


def read_csv_name_set(csv_path: Path) -> tuple[set[str], int]:
    rows = parse_tabular_csv(csv_path)
    names = {
        normalize_text(clean_text(row.get("english", "")))
        for row in rows
        if clean_text(row.get("english", "")) and clean_text(row.get("english", "")) != "(None)"
    }
    return names, len(rows)


def request_json(params: dict[str, str], *, use_post: bool = False, retry: int = 3) -> dict:
    encoded = urlencode(params).encode("utf-8")
    error: Exception | None = None
    for attempt in range(retry):
        try:
            if use_post:
                request = Request(
                    BASE_API_URL,
                    data=encoded,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    },
                )
            else:
                request = Request(
                    BASE_API_URL + "?" + encoded.decode("utf-8"),
                    headers={"User-Agent": USER_AGENT},
                )
            with urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # pragma: no cover
            error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"请求 Nookipedia 失败：{error}")


def cargo_query_all(
    *,
    tables: str,
    fields: str,
    where: str | None = None,
    join_on: str | None = None,
    order_by: str | None = None,
    limit: int = 500,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    offset = 0
    while True:
        params = {
            "action": "cargoquery",
            "format": "json",
            "tables": tables,
            "fields": fields,
            "limit": str(limit),
            "offset": str(offset),
        }
        if where:
            params["where"] = where
        if join_on:
            params["join_on"] = join_on
        if order_by:
            params["order_by"] = order_by
        payload = request_json(params)
        chunk = payload.get("cargoquery", [])
        if not chunk:
            break
        for item in chunk:
            row = {key.replace(" ", "_"): clean_text(value) for key, value in item.get("title", {}).items()}
            rows.append(row)
        if len(chunk) < limit:
            break
        offset += len(chunk)
        time.sleep(0.08)
    return rows


def batch_fetch_extracts(page_titles: set[str], progress: ProgressCallback | None = None) -> dict[str, str]:
    titles = sorted({clean_text(title) for title in page_titles if clean_text(title)})
    extracts: dict[str, str] = {}
    if not titles:
        return extracts

    emit(progress, f"正在抓取 Wiki 摘要，共 {len(titles)} 个页面")
    batch_size = 20
    for start in range(0, len(titles), batch_size):
        chunk = titles[start : start + batch_size]
        payload = request_json(
            {
                "action": "query",
                "format": "json",
                "prop": "extracts",
                "exintro": "1",
                "explaintext": "1",
                "titles": "|".join(chunk),
            },
            use_post=True,
        )
        pages = payload.get("query", {}).get("pages", {})
        for page in pages.values():
            title = clean_text(page.get("title", ""))
            extract = clean_text(page.get("extract", ""))
            if title:
                extracts[title] = re.sub(r"\n{2,}", "\n\n", extract)
        if (start // batch_size) % 8 == 0:
            emit(progress, f"已抓取摘要 {min(start + len(chunk), len(titles))}/{len(titles)}")
        time.sleep(0.08)
    return extracts


def download_image(url: str, images_dir: Path) -> tuple[str, str] | None:
    suffix = guess_image_suffix(url)
    if suffix is None:
        return None
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    relative_path = f"{IMAGES_DIR_NAME}/{digest}{suffix}"
    target = images_dir / f"{digest}{suffix}"
    if target.exists():
        return url, relative_path
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=90) as response:
        target.write_bytes(response.read())
    return url, relative_path


def download_images(
    urls: set[str],
    cache_dir: Path,
    progress: ProgressCallback | None = None,
    workers: int = 8,
) -> dict[str, str]:
    filtered_urls = sorted({url for url in urls if clean_text(url) and guess_image_suffix(url)})
    images_dir = cache_dir / IMAGES_DIR_NAME
    images_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    if not filtered_urls:
        return mapping

    emit(progress, f"正在缓存图标，共 {len(filtered_urls)} 张")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_image, url, images_dir): url for url in filtered_urls}
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            if result is not None:
                original_url, relative_path = result
                mapping[original_url] = relative_path
            if completed == 1 or completed % 100 == 0 or completed == len(filtered_urls):
                emit(progress, f"已缓存图标 {completed}/{len(filtered_urls)}")
    return mapping


def register_item_entry(
    item_entries: dict[str, dict[str, str]],
    item_lookup: dict[str, tuple[int, str]],
    entry: dict[str, str],
    aliases: list[tuple[str, int]],
) -> None:
    entry_id = entry["id"]
    item_entries[entry_id] = entry
    for alias, priority in aliases:
        normalized = normalize_text(alias)
        if not normalized:
            continue
        existing = item_lookup.get(normalized)
        if existing is None or priority >= existing[0]:
            item_lookup[normalized] = (priority, entry_id)


def register_section_entry(
    encyclopedia: dict[str, list[dict[str, str]]],
    section_id: str,
    entry: dict[str, str],
) -> None:
    encyclopedia.setdefault(section_id, []).append(entry)


def build_item_aliases(
    name: str,
    variation: str = "",
    pattern: str = "",
    *,
    include_recipe_alias: bool = False,
) -> list[tuple[str, int]]:
    aliases: list[tuple[str, int]] = [(name, 90)]
    if variation:
        aliases.append((f"{name} ({variation})", 120))
    if pattern:
        aliases.append((f"{name} ({pattern})", 115))
    if variation and pattern:
        aliases.append((f"{name} ({variation}, {pattern})", 125))
    if include_recipe_alias:
        aliases.append((f"{name} (DIY recipe)", 110))
    return aliases


def build_other_item_facts(row: dict[str, str]) -> str:
    return build_multiline_facts(
        [
            ("堆叠数", row.get("stack")),
            ("卖价", format_bells(row.get("sell"))),
            ("HHA", row.get("hha_base")),
            ("素材类型", row.get("material_type")),
            ("植物类型", row.get("plant_type")),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
            ("备注", row.get("notes")),
        ]
    )


def build_recipe_materials(row: dict[str, str]) -> str:
    materials: list[str] = []
    for index in range(1, 7):
        material = clean_text(row.get(f"material{index}"))
        amount = clean_text(row.get(f"material{index}_num"))
        if material:
            materials.append(f"{material} × {amount or '1'}")
    return ", ".join(materials)


def build_recipe_facts(row: dict[str, str]) -> str:
    return build_multiline_facts(
        [
            ("材料", build_recipe_materials(row)),
            ("卖价", format_bells(row.get("sell"))),
            ("解锁方式", row.get("recipes_to_unlock")),
            ("获取方式 1", row.get("diy_availability1")),
            ("说明 1", row.get("diy_availability1_note")),
            ("获取方式 2", row.get("diy_availability2")),
            ("说明 2", row.get("diy_availability2_note")),
        ]
    )


def build_art_facts(row: dict[str, str], fake_label: str = "") -> str:
    return build_multiline_facts(
        [
            ("艺术品原名", row.get("art_name")),
            ("类型", row.get("art_type")),
            ("作者", row.get("author")),
            ("年份", row.get("year")),
            ("风格", row.get("art_style")),
            ("真伪说明", row.get("authenticity")),
            ("获取方式", row.get("availability")),
            ("卖价", format_bells(row.get("sell"))),
            ("图片", fake_label),
        ]
    )


def build_clothing_facts(row: dict[str, str], variation_row: dict[str, str] | None = None) -> str:
    variation_label = variation_row.get("variation") if variation_row else ""
    colors = ""
    if variation_row is not None:
        colors = " / ".join(filter(None, [variation_row.get("color1", ""), variation_row.get("color2", "")]))
    return build_multiline_facts(
        [
            ("分类", row.get("category")),
            ("变体", variation_label),
            ("颜色", colors),
            ("风格 1", row.get("style1")),
            ("风格 2", row.get("style2")),
            ("季节", row.get("seasonality")),
            ("买价", format_bells(row.get("buy1_price"))),
            ("卖价", format_bells(row.get("sell"))),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
            ("备注", row.get("notes")),
        ]
    )


def build_furniture_facts(row: dict[str, str], variation_row: dict[str, str] | None = None) -> str:
    variation_label = variation_row.get("variation") if variation_row else ""
    colors = ""
    if variation_row is not None:
        colors = " / ".join(filter(None, [variation_row.get("color1", ""), variation_row.get("color2", "")]))
    return build_multiline_facts(
        [
            ("分类", row.get("category")),
            ("变体", variation_label),
            ("系列", row.get("item_series")),
            ("套装", row.get("item_set")),
            ("主题 1", row.get("theme1")),
            ("主题 2", row.get("theme2")),
            ("颜色", colors),
            ("买价", format_bells(row.get("buy1_price"))),
            ("卖价", format_bells(row.get("sell"))),
            ("可改造", row.get("customizable")),
            ("尺寸", row.get("grid_size")),
            ("高度", row.get("height")),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("获取方式 3", row.get("availability3")),
            ("说明 3", row.get("availability3_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
            ("备注", row.get("notes")),
        ]
    )


def build_interior_facts(row: dict[str, str]) -> str:
    return build_multiline_facts(
        [
            ("分类", row.get("category")),
            ("系列", row.get("item_series")),
            ("套装", row.get("item_set")),
            ("主题 1", row.get("theme1")),
            ("主题 2", row.get("theme2")),
            ("颜色 1", row.get("color1")),
            ("颜色 2", row.get("color2")),
            ("买价", format_bells(row.get("buy1_price"))),
            ("卖价", format_bells(row.get("sell"))),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
            ("备注", row.get("notes")),
        ]
    )


def build_tool_facts(row: dict[str, str], variation_row: dict[str, str] | None = None) -> str:
    return build_multiline_facts(
        [
            ("变体", variation_row.get("variation") if variation_row else ""),
            ("耐久", row.get("uses")),
            ("买价", format_bells(row.get("buy1_price"))),
            ("卖价", format_bells(row.get("sell"))),
            ("可改造", row.get("customizable")),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("获取方式 3", row.get("availability3")),
            ("说明 3", row.get("availability3_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
            ("备注", row.get("notes")),
        ]
    )


def build_photo_facts(row: dict[str, str], variation_row: dict[str, str] | None = None) -> str:
    colors = ""
    if variation_row is not None:
        colors = " / ".join(filter(None, [variation_row.get("color1", ""), variation_row.get("color2", "")]))
    return build_multiline_facts(
        [
            ("分类", row.get("category")),
            ("变体", variation_row.get("variation") if variation_row else ""),
            ("颜色", colors),
            ("买价", format_bells(row.get("buy1_price"))),
            ("卖价", format_bells(row.get("sell"))),
            ("互动", row.get("interactable")),
            ("尺寸", row.get("grid_size")),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
        ]
    )


def build_gyroid_facts(row: dict[str, str], variation_row: dict[str, str] | None = None) -> str:
    colors = ""
    if variation_row is not None:
        colors = " / ".join(filter(None, [variation_row.get("color1", ""), variation_row.get("color2", "")]))
    return build_multiline_facts(
        [
            ("变体", variation_row.get("variation") if variation_row else ""),
            ("颜色", colors),
            ("声音", row.get("sound")),
            ("卖价", format_bells(row.get("sell"))),
            ("Cyrus 费用", format_bells(row.get("cyrus_price"))),
            ("可改造", row.get("customizable")),
            ("尺寸", row.get("grid_size")),
            ("获取方式 1", row.get("availability1")),
            ("说明 1", row.get("availability1_note")),
            ("获取方式 2", row.get("availability2")),
            ("说明 2", row.get("availability2_note")),
            ("获取方式 3", row.get("availability3")),
            ("说明 3", row.get("availability3_note")),
            ("解锁条件", row.get("unlocked")),
            ("版本", row.get("version_added")),
            ("备注", row.get("notes")),
        ]
    )


def build_critter_facts(row: dict[str, str], *, include_weather: bool = False, include_shadow: bool = False, include_movement: bool = False) -> str:
    return build_multiline_facts(
        [
            ("编号", row.get("number")),
            ("地点", row.get("location")),
            ("天气", row.get("weather") if include_weather else ""),
            ("影子大小", row.get("shadow_size") if include_shadow else ""),
            ("移动方式", row.get("shadow_movement") if include_movement else ""),
            ("稀有度", row.get("rarity")),
            ("价格", format_bells(row.get("sell_nook"))),
            ("CJ 价格", format_bells(row.get("sell_cj"))),
            ("Flick 价格", format_bells(row.get("sell_flick"))),
            ("时间", row.get("time")),
            ("北半球月份", row.get("n_availability")),
            ("南半球月份", row.get("s_availability")),
            ("名言 1", row.get("catchphrase")),
            ("名言 2", row.get("catchphrase2")),
            ("名言 3", row.get("catchphrase3")),
        ]
    )


def build_fossil_facts(row: dict[str, str]) -> str:
    return build_multiline_facts(
        [
            ("化石组", row.get("fossil_group")),
            ("可互动", row.get("interactable")),
            ("卖价", format_bells(row.get("sell"))),
            ("颜色 1", row.get("color1")),
            ("颜色 2", row.get("color2")),
            ("尺寸", " × ".join(filter(None, [row.get("width"), row.get("length")]))),
        ]
    )


def build_villager_facts(row: dict[str, str]) -> str:
    birthday = " ".join(filter(None, [row.get("birthday_month"), row.get("birthday_day")])).strip()
    favorite_style = " / ".join(filter(None, [row.get("fav_style1"), row.get("fav_style2")]))
    favorite_color = " / ".join(filter(None, [row.get("fav_color1"), row.get("fav_color2")]))
    return build_multiline_facts(
        [
            ("物种", row.get("species")),
            ("性格", row.get("personality")),
            ("性别", row.get("gender")),
            ("生日", birthday),
            ("爱好", row.get("nh_hobby")),
            ("常用语", row.get("nh_catchphrase") or row.get("phrase")),
            ("座右铭", row.get("quote")),
            ("偏好风格", favorite_style),
            ("偏好颜色", favorite_color),
        ]
    )


def build_event_facts(row: dict[str, str]) -> str:
    return build_multiline_facts(
        [
            ("日期", row.get("date")),
            ("类型", row.get("type")),
            ("链接页面", row.get("page_title")),
        ]
    )


def build_subtitle(parts: list[str]) -> str:
    return " | ".join([clean_text(part) for part in parts if clean_text(part)])


def make_item_entry(
    *,
    entry_id: str,
    title: str,
    dataset_id: str,
    page_title: str,
    image_url: str,
    facts_text: str,
) -> dict[str, str]:
    return {
        "id": entry_id,
        "title": clean_text(title),
        "dataset_id": dataset_id,
        "dataset_label": ITEM_DATASET_LABELS[dataset_id],
        "page_title": clean_text(page_title),
        "wiki_url": format_wiki_url(page_title) if page_title else "",
        "summary": "",
        "facts_text": facts_text,
        "image_url": clean_text(image_url),
        "image_rel_path": "",
    }


def make_section_entry(
    *,
    entry_id: str,
    title: str,
    subtitle: str,
    page_title: str,
    image_url: str,
    facts_text: str,
    section_id: str,
) -> dict[str, str]:
    return {
        "id": entry_id,
        "title": clean_text(title),
        "subtitle": clean_text(subtitle),
        "page_title": clean_text(page_title),
        "wiki_url": format_wiki_url(page_title) if page_title else "",
        "summary": "",
        "facts_text": facts_text,
        "image_url": clean_text(image_url),
        "image_rel_path": "",
        "section_id": section_id,
        "section_label": SECTION_LABELS[section_id],
    }


def sync_offline_data(
    *,
    csv_path: Path,
    cache_dir: Path,
    download_images_enabled: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_names, csv_row_count = read_csv_name_set(csv_path)
    emit(progress, f"已读取 CSV，共 {csv_row_count} 行，唯一英文名 {len(csv_names)} 个")

    item_entries: dict[str, dict[str, str]] = {}
    item_lookup: dict[str, tuple[int, str]] = {}
    encyclopedia: dict[str, list[dict[str, str]]] = {section_id: [] for section_id in SECTION_LABELS}
    page_titles: set[str] = set()
    image_urls: set[str] = set()
    item_counter = 0
    section_counter = 0

    def next_item_id() -> str:
        nonlocal item_counter
        item_counter += 1
        return f"item-{item_counter:05d}"

    def next_section_id(section_id: str) -> str:
        nonlocal section_counter
        section_counter += 1
        return f"{section_id}-{section_counter:05d}"

    def register_item_if_needed(entry: dict[str, str], aliases: list[tuple[str, int]]) -> None:
        if not any(normalize_text(alias) in csv_names for alias, _ in aliases):
            return
        register_item_entry(item_entries, item_lookup, entry, aliases)
        if entry["page_title"]:
            page_titles.add(entry["page_title"])
        if entry["image_url"]:
            image_urls.add(entry["image_url"])

    def register_section(entry: dict[str, str]) -> None:
        register_section_entry(encyclopedia, entry["section_id"], entry)
        if entry["page_title"]:
            page_titles.add(entry["page_title"])
        if entry["image_url"]:
            image_urls.add(entry["image_url"])

    emit(progress, "正在抓取材料与杂项")
    other_items = cargo_query_all(
        tables="nh_item",
        fields="_pageName=page_title,en_name=name,image_url,stack,hha_base,buy1_price,sell,is_fence,material_type,plant_type,availability1,availability1_note,availability2,availability2_note,unlocked,version_added,notes",
    )
    for row in other_items:
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="other_items",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=build_other_item_facts(row),
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))

    emit(progress, "正在抓取 DIY 配方")
    recipes = cargo_query_all(
        tables="nh_recipe",
        fields="_pageName=page_title,en_name=name,image_url,sell,recipes_to_unlock,diy_availability1,diy_availability1_note,diy_availability2,diy_availability2_note,material1,material1_num,material2,material2_num,material3,material3_num,material4,material4_num,material5,material5_num,material6,material6_num",
    )
    for row in recipes:
        facts = build_recipe_facts(row)
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="recipes",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", ""), include_recipe_alias=True))
        register_section(
            make_section_entry(
                entry_id=next_section_id("recipes"),
                title=row.get("name", ""),
                subtitle=build_subtitle([format_bells(row.get("sell")), build_recipe_materials(row)]),
                page_title=row.get("page_title", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="recipes",
            )
        )

    emit(progress, "正在抓取艺术品")
    art_rows = cargo_query_all(
        tables="nh_art",
        fields="_pageName=page_title,name,image_url,fake_image_url,has_fake,art_name,art_type,author,year,art_style,description,sell,availability,authenticity",
    )
    for row in art_rows:
        facts = build_art_facts(row)
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="art",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))
        register_section(
            make_section_entry(
                entry_id=next_section_id("art"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("author", ""), row.get("art_type", ""), "有赝品" if row.get("has_fake") == "1" else "无赝品"]),
                page_title=row.get("page_title", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="art",
            )
        )
        if row.get("fake_image_url"):
            fake_entry = make_item_entry(
                entry_id=next_item_id(),
                title=f"{row.get('name', '')} (forgery)",
                dataset_id="art",
                page_title=row.get("page_title", ""),
                image_url=row.get("fake_image_url", ""),
                facts_text=build_art_facts(row, fake_label="赝品图"),
            )
            register_item_if_needed(fake_entry, build_item_aliases(f"{row.get('name', '')} (forgery)"))

    emit(progress, "正在抓取服装与服装变体")
    clothing_rows = cargo_query_all(
        tables="nh_clothing",
        fields="_pageName=page_title,en_name=name,category,style1,style2,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,seasonality,unlocked,version_added,notes",
    )
    clothing_variations = cargo_query_all(
        tables="nh_clothing_variation",
        fields="en_name=name,variation,image_url,color1,color2",
        order_by="variation_number",
    )
    clothing_variation_map: dict[str, list[dict[str, str]]] = {}
    for row in clothing_variations:
        clothing_variation_map.setdefault(row.get("name", ""), []).append(row)
    for row in clothing_rows:
        variations = clothing_variation_map.get(row.get("name", ""), [])
        base_image = variations[0].get("image_url", "") if variations else ""
        base_entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="clothing",
            page_title=row.get("page_title", ""),
            image_url=base_image,
            facts_text=build_clothing_facts(row, variations[0] if variations else None),
        )
        register_item_if_needed(base_entry, build_item_aliases(row.get("name", "")))
        for variation in variations:
            variation_entry = make_item_entry(
                entry_id=next_item_id(),
                title=f"{row.get('name', '')} ({variation.get('variation', '')})",
                dataset_id="clothing",
                page_title=row.get("page_title", ""),
                image_url=variation.get("image_url", ""),
                facts_text=build_clothing_facts(row, variation),
            )
            register_item_if_needed(
                variation_entry,
                build_item_aliases(row.get("name", ""), variation.get("variation", "")),
            )

    emit(progress, "正在抓取家具与家具变体")
    furniture_rows = cargo_query_all(
        tables="nh_furniture",
        fields="_pageName=page_title,en_name=name,category,item_series,item_set,theme1,theme2,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,availability3,availability3_note,customizable,grid_size,height,unlocked,version_added,notes",
    )
    furniture_variations = cargo_query_all(
        tables="nh_furniture_variation",
        fields="en_name=name,variation,pattern,image_url,color1,color2",
        order_by="variation_number,pattern_number",
    )
    furniture_variation_map: dict[str, list[dict[str, str]]] = {}
    for row in furniture_variations:
        furniture_variation_map.setdefault(row.get("name", ""), []).append(row)
    for row in furniture_rows:
        variations = furniture_variation_map.get(row.get("name", ""), [])
        base_image = variations[0].get("image_url", "") if variations else ""
        base_entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="furniture",
            page_title=row.get("page_title", ""),
            image_url=base_image,
            facts_text=build_furniture_facts(row, variations[0] if variations else None),
        )
        register_item_if_needed(base_entry, build_item_aliases(row.get("name", "")))
        for variation in variations:
            variation_entry = make_item_entry(
                entry_id=next_item_id(),
                title=f"{row.get('name', '')} ({variation.get('variation', '')})",
                dataset_id="furniture",
                page_title=row.get("page_title", ""),
                image_url=variation.get("image_url", ""),
                facts_text=build_furniture_facts(row, variation),
            )
            register_item_if_needed(
                variation_entry,
                build_item_aliases(row.get("name", ""), variation.get("variation", ""), variation.get("pattern", "")),
            )

    emit(progress, "正在抓取墙纸/地板/地毯")
    interior_rows = cargo_query_all(
        tables="nh_interior",
        fields="_pageName=page_title,en_name=name,image_url,category,item_series,item_set,theme1,theme2,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,color1,color2,unlocked,version_added,notes",
    )
    for row in interior_rows:
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="interior",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=build_interior_facts(row),
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))

    emit(progress, "正在抓取工具与工具变体")
    tool_rows = cargo_query_all(
        tables="nh_tool",
        fields="_pageName=page_title,en_name=name,uses,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,availability3,availability3_note,customizable,unlocked,version_added,notes",
    )
    tool_variations = cargo_query_all(
        tables="nh_tool_variation",
        fields="en_name=name,variation,image_url",
        order_by="variation_number",
    )
    tool_variation_map: dict[str, list[dict[str, str]]] = {}
    for row in tool_variations:
        tool_variation_map.setdefault(row.get("name", ""), []).append(row)
    for row in tool_rows:
        variations = tool_variation_map.get(row.get("name", ""), [])
        base_image = variations[0].get("image_url", "") if variations else ""
        base_entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="tools",
            page_title=row.get("page_title", ""),
            image_url=base_image,
            facts_text=build_tool_facts(row, variations[0] if variations else None),
        )
        register_item_if_needed(base_entry, build_item_aliases(row.get("name", "")))
        for variation in variations:
            variation_entry = make_item_entry(
                entry_id=next_item_id(),
                title=f"{row.get('name', '')} ({variation.get('variation', '')})",
                dataset_id="tools",
                page_title=row.get("page_title", ""),
                image_url=variation.get("image_url", ""),
                facts_text=build_tool_facts(row, variation),
            )
            register_item_if_needed(
                variation_entry,
                build_item_aliases(row.get("name", ""), variation.get("variation", "")),
            )

    emit(progress, "正在抓取照片/海报")
    photo_rows = cargo_query_all(
        tables="nh_photo",
        fields="_pageName=page_title,en_name=name,category,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,interactable,grid_size,unlocked,version_added",
    )
    photo_variations = cargo_query_all(
        tables="nh_photo_variation",
        fields="en_name=name,variation,image_url,color1,color2",
        order_by="variation_number",
    )
    photo_variation_map: dict[str, list[dict[str, str]]] = {}
    for row in photo_variations:
        photo_variation_map.setdefault(row.get("name", ""), []).append(row)
    for row in photo_rows:
        variations = photo_variation_map.get(row.get("name", ""), [])
        base_image = variations[0].get("image_url", "") if variations else ""
        base_entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="photos",
            page_title=row.get("page_title", ""),
            image_url=base_image,
            facts_text=build_photo_facts(row, variations[0] if variations else None),
        )
        register_item_if_needed(base_entry, build_item_aliases(row.get("name", "")))
        for variation in variations:
            variation_entry = make_item_entry(
                entry_id=next_item_id(),
                title=f"{row.get('name', '')} ({variation.get('variation', '')})",
                dataset_id="photos",
                page_title=row.get("page_title", ""),
                image_url=variation.get("image_url", ""),
                facts_text=build_photo_facts(row, variation),
            )
            register_item_if_needed(
                variation_entry,
                build_item_aliases(row.get("name", ""), variation.get("variation", "")),
            )

    emit(progress, "正在抓取陀螺仪")
    gyroid_rows = cargo_query_all(
        tables="nh_gyroid",
        fields="_pageName=page_title,en_name=name,sell,cyrus_price,availability1,availability1_note,availability2,availability2_note,availability3,availability3_note,customizable,grid_size,sound,unlocked,version_added,notes",
    )
    gyroid_variations = cargo_query_all(
        tables="nh_gyroid_variation",
        fields="en_name=name,variation,image_url,color1,color2",
        order_by="variation_number",
    )
    gyroid_variation_map: dict[str, list[dict[str, str]]] = {}
    for row in gyroid_variations:
        gyroid_variation_map.setdefault(row.get("name", ""), []).append(row)
    for row in gyroid_rows:
        variations = gyroid_variation_map.get(row.get("name", ""), [])
        base_image = variations[0].get("image_url", "") if variations else ""
        base_entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="gyroids",
            page_title=row.get("page_title", ""),
            image_url=base_image,
            facts_text=build_gyroid_facts(row, variations[0] if variations else None),
        )
        register_item_if_needed(base_entry, build_item_aliases(row.get("name", "")))
        for variation in variations:
            variation_entry = make_item_entry(
                entry_id=next_item_id(),
                title=f"{row.get('name', '')} ({variation.get('variation', '')})",
                dataset_id="gyroids",
                page_title=row.get("page_title", ""),
                image_url=variation.get("image_url", ""),
                facts_text=build_gyroid_facts(row, variation),
            )
            register_item_if_needed(
                variation_entry,
                build_item_aliases(row.get("name", ""), variation.get("variation", "")),
            )

    emit(progress, "正在抓取鱼类图鉴")
    fish_rows = cargo_query_all(
        tables="nh_fish",
        fields="name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,catchphrase3,location,shadow_size,rarity,sell_nook,time,n_availability,s_availability",
    )
    for row in fish_rows:
        facts = build_critter_facts(row, include_shadow=True)
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="fish",
            page_title=row.get("page_title", "") or row.get("name", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))
        register_section(
            make_section_entry(
                entry_id=next_section_id("fish"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("location", ""), row.get("rarity", ""), format_bells(row.get("sell_nook"))]),
                page_title=row.get("page_title", "") or row.get("name", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="fish",
            )
        )

    emit(progress, "正在抓取昆虫图鉴")
    bug_rows = cargo_query_all(
        tables="nh_bug",
        fields="name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,location,weather,rarity,sell_nook,sell_flick,time,n_availability,s_availability",
    )
    for row in bug_rows:
        facts = build_critter_facts(row, include_weather=True)
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="bugs",
            page_title=row.get("page_title", "") or row.get("name", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))
        register_section(
            make_section_entry(
                entry_id=next_section_id("bugs"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("location", ""), row.get("rarity", ""), format_bells(row.get("sell_nook"))]),
                page_title=row.get("page_title", "") or row.get("name", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="bugs",
            )
        )

    emit(progress, "正在抓取海洋生物图鉴")
    sea_rows = cargo_query_all(
        tables="nh_sea_creature",
        fields="name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,shadow_size,shadow_movement,rarity,sell_nook,time,n_availability,s_availability",
    )
    for row in sea_rows:
        facts = build_critter_facts(row, include_shadow=True, include_movement=True)
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="sea",
            page_title=row.get("page_title", "") or row.get("name", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))
        register_section(
            make_section_entry(
                entry_id=next_section_id("sea"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("shadow_size", ""), row.get("rarity", ""), format_bells(row.get("sell_nook"))]),
                page_title=row.get("page_title", "") or row.get("name", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="sea",
            )
        )

    emit(progress, "正在抓取化石")
    fossil_rows = cargo_query_all(
        tables="nh_fossil",
        fields="name,_pageName=page_title,image_url,fossil_group,interactable,sell,color1,color2,width,length",
    )
    for row in fossil_rows:
        facts = build_fossil_facts(row)
        entry = make_item_entry(
            entry_id=next_item_id(),
            title=row.get("name", ""),
            dataset_id="fossils",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(entry, build_item_aliases(row.get("name", "")))
        register_section(
            make_section_entry(
                entry_id=next_section_id("fossils"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("fossil_group", ""), format_bells(row.get("sell"))]),
                page_title=row.get("page_title", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="fossils",
            )
        )

    emit(progress, "正在抓取村民资料")
    villager_rows = cargo_query_all(
        tables="villager,nh_villager",
        join_on="villager._pageName=nh_villager._pageName",
        where='villager.nh="1"',
        fields="villager.name=name,villager._pageName=page_title,villager.species,villager.personality,villager.gender,villager.birthday_month,villager.birthday_day,villager.quote,villager.phrase,nh_villager.icon_url=image_url,nh_villager.catchphrase=nh_catchphrase,nh_villager.hobby=nh_hobby,nh_villager.fav_style1=fav_style1,nh_villager.fav_style2=fav_style2,nh_villager.fav_color1=fav_color1,nh_villager.fav_color2=fav_color2",
    )
    for row in villager_rows:
        facts = build_villager_facts(row)
        birthday = " ".join(filter(None, [row.get("birthday_month"), row.get("birthday_day")]))
        register_section(
            make_section_entry(
                entry_id=next_section_id("villagers"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("species", ""), row.get("personality", ""), birthday]),
                page_title=row.get("page_title", "") or row.get("name", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="villagers",
            )
        )

    emit(progress, "正在抓取活动日历")
    current_year = datetime.now().year
    next_year = current_year + 1
    event_rows = cargo_query_all(
        tables="nh_calendar",
        fields="event,date,type,link=page_title",
        where=f'YEAR(date)="{current_year}" OR YEAR(date)="{next_year}"',
        order_by="date",
    )
    for row in event_rows:
        register_section(
            make_section_entry(
                entry_id=next_section_id("events"),
                title=row.get("event", ""),
                subtitle=build_subtitle([row.get("date", ""), row.get("type", "")]),
                page_title=row.get("page_title", ""),
                image_url="",
                facts_text=build_event_facts(row),
                section_id="events",
            )
        )

    extracts = batch_fetch_extracts(page_titles, progress=progress)
    for entry in item_entries.values():
        entry["summary"] = extracts.get(entry["page_title"], "")
    for section_entries in encyclopedia.values():
        for entry in section_entries:
            entry["summary"] = extracts.get(entry["page_title"], "")

    image_map: dict[str, str] = {}
    if download_images_enabled:
        image_map = download_images(image_urls, cache_dir, progress=progress)

    for entry in item_entries.values():
        entry["image_rel_path"] = image_map.get(entry["image_url"], "")
        del entry["image_url"]
    for section_entries in encyclopedia.values():
        for entry in section_entries:
            entry["image_rel_path"] = image_map.get(entry["image_url"], "")
            del entry["image_url"]

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {
            "wiki": "https://nookipedia.com/wiki/Main_Page",
            "api": BASE_API_URL,
            "note": "Nookipedia 离线缓存，仅供本地查看。",
        },
        "stats": {
            "csv_row_count": csv_row_count,
            "csv_unique_english_names": len(csv_names),
            "matched_item_entries": len({entry_id for _, entry_id in item_lookup.values()}),
            "item_aliases": len(item_lookup),
            "section_counts": {section_id: len(entries) for section_id, entries in encyclopedia.items()},
            "downloaded_images": len(image_map),
        },
        "item_entries": item_entries,
        "item_lookup": {key: entry_id for key, (_, entry_id) in item_lookup.items()},
        "encyclopedia": encyclopedia,
    }

    cache_file = cache_dir / CACHE_FILE_NAME
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(progress, f"离线知识库已写入：{cache_file}")
    return result


def empty_payload() -> dict[str, object]:
    return {
        "generated_at": "",
        "source": {
            "wiki": "https://nookipedia.com/wiki/Main_Page",
            "api": BASE_API_URL,
            "note": "Nookipedia 离线缓存，仅供本地查看。",
        },
        "stats": {
            "matched_item_entries": 0,
            "item_aliases": 0,
            "downloaded_images": 0,
            "section_counts": {section_id: 0 for section_id in SECTION_LABELS},
        },
        "item_entries": {},
        "item_lookup": {},
        "encyclopedia": {section_id: [] for section_id in SECTION_LABELS},
    }


def load_payload(cache_dir: Path) -> dict[str, object]:
    cache_file = cache_dir / CACHE_FILE_NAME
    if not cache_file.exists():
        return empty_payload()
    return json.loads(cache_file.read_text(encoding="utf-8"))


def save_payload(cache_dir: Path, payload: dict[str, object]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / CACHE_FILE_NAME
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_payload_stats(payload: dict[str, object], cache_dir: Path) -> None:
    item_entries = dict(payload.get("item_entries", {}))
    item_lookup = dict(payload.get("item_lookup", {}))
    encyclopedia = dict(payload.get("encyclopedia", {}))
    image_rel_paths = set()
    for entry in item_entries.values():
        if entry.get("image_rel_path"):
            image_rel_paths.add(entry["image_rel_path"])
    for section_entries in encyclopedia.values():
        for entry in section_entries:
            if entry.get("image_rel_path"):
                image_rel_paths.add(entry["image_rel_path"])
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["stats"] = {
        "matched_item_entries": len(item_entries),
        "item_aliases": len(item_lookup),
        "downloaded_images": len(image_rel_paths),
        "section_counts": {section_id: len(encyclopedia.get(section_id, [])) for section_id in SECTION_LABELS},
        "cache_dir": str(cache_dir),
    }


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{value}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def enrich_entries_with_assets(
    entries: list[dict[str, str]],
    cache_dir: Path,
    *,
    download_images_enabled: bool,
    progress: ProgressCallback | None = None,
) -> None:
    page_titles = {entry.get("page_title", "") for entry in entries if entry.get("page_title") and not entry.get("summary")}
    image_urls = {entry.get("image_url", "") for entry in entries if entry.get("image_url") and not entry.get("image_rel_path")}
    extracts = batch_fetch_extracts(page_titles, progress=progress) if page_titles else {}
    image_map = download_images(image_urls, cache_dir, progress=progress) if download_images_enabled and image_urls else {}
    for entry in entries:
        if not entry.get("summary"):
            entry["summary"] = extracts.get(entry.get("page_title", ""), "")
        if not entry.get("image_rel_path"):
            entry["image_rel_path"] = image_map.get(entry.get("image_url", ""), "")
        if "image_url" in entry:
            del entry["image_url"]


def escape_cargo_value(value: str) -> str:
    return clean_text(value).replace("\\", "\\\\").replace('"', '\\"')


def cargo_query_first(*, tables: str, fields: str, where: str, join_on: str | None = None, order_by: str | None = None) -> dict[str, str] | None:
    rows = cargo_query_all(tables=tables, fields=fields, where=where, join_on=join_on, order_by=order_by, limit=10)
    return rows[0] if rows else None


def match_variation_row(rows: list[dict[str, str]], token: str) -> dict[str, str] | None:
    normalized_token = normalize_text(token)
    for row in rows:
        variation = row.get("variation", "")
        pattern = row.get("pattern", "")
        candidates = [variation, pattern]
        if variation and pattern:
            candidates.append(f"{variation}, {pattern}")
        if any(normalize_text(candidate) == normalized_token for candidate in candidates if candidate):
            return row
    return rows[0] if rows else None


def sync_helper_cache(
    *,
    csv_path: Path,
    cache_dir: Path,
    download_images_enabled: bool = True,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    payload = load_payload(cache_dir)
    item_entries = dict(payload.get("item_entries", {}))
    item_lookup = {key: (100, entry_id) for key, entry_id in dict(payload.get("item_lookup", {})).items()}
    encyclopedia: dict[str, list[dict[str, str]]] = {section_id: [] for section_id in SECTION_LABELS}
    csv_names, csv_row_count = read_csv_name_set(csv_path)
    emit(progress, f"已读取 CSV，共 {csv_row_count} 行，唯一英文名 {len(csv_names)} 个")

    new_entries: list[dict[str, str]] = []

    def register_item_if_needed(entry: dict[str, str], aliases: list[tuple[str, int]]) -> None:
        if any(normalize_text(alias) in csv_names for alias, _ in aliases):
            register_item_entry(item_entries, item_lookup, entry, aliases)
            new_entries.append(entry)

    def add_section(section_id: str, entry: dict[str, str]) -> None:
        encyclopedia[section_id].append(entry)
        new_entries.append(entry)

    emit(progress, "正在同步轻量离线百科")

    recipes = cargo_query_all(
        tables="nh_recipe",
        fields="_pageName=page_title,en_name=name,image_url,sell,recipes_to_unlock,diy_availability1,diy_availability1_note,diy_availability2,diy_availability2_note,material1,material1_num,material2,material2_num,material3,material3_num,material4,material4_num,material5,material5_num,material6,material6_num",
    )
    for row in recipes:
        facts = build_recipe_facts(row)
        item_entry = make_item_entry(
            entry_id=stable_id("item", f"recipe:{row.get('name', '')}"),
            title=row.get("name", ""),
            dataset_id="recipes",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(item_entry, build_item_aliases(row.get("name", ""), include_recipe_alias=True))
        add_section(
            "recipes",
            make_section_entry(
                entry_id=stable_id("section", f"recipes:{row.get('name', '')}"),
                title=row.get("name", ""),
                subtitle=build_subtitle([format_bells(row.get("sell")), build_recipe_materials(row)]),
                page_title=row.get("page_title", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="recipes",
            ),
        )

    art_rows = cargo_query_all(
        tables="nh_art",
        fields="_pageName=page_title,name,image_url,has_fake,fake_image_url,art_name,art_type,author,year,art_style,description,sell,availability,authenticity",
    )
    for row in art_rows:
        facts = build_art_facts(row)
        item_entry = make_item_entry(
            entry_id=stable_id("item", f"art:{row.get('name', '')}"),
            title=row.get("name", ""),
            dataset_id="art",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(item_entry, build_item_aliases(row.get("name", "")))
        add_section(
            "art",
            make_section_entry(
                entry_id=stable_id("section", f"art:{row.get('name', '')}"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("author", ""), row.get("art_type", ""), "有赝品" if row.get("has_fake") == "1" else "无赝品"]),
                page_title=row.get("page_title", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="art",
            ),
        )

    for section_id, table_name, subtitle_builder, item_dataset, fact_builder, extra_kwargs in [
        ("fish", "nh_fish", lambda row: build_subtitle([row.get("location", ""), row.get("rarity", ""), format_bells(row.get("sell_nook"))]), "fish", lambda row: build_critter_facts(row, include_shadow=True), {}),
        ("bugs", "nh_bug", lambda row: build_subtitle([row.get("location", ""), row.get("rarity", ""), format_bells(row.get("sell_nook"))]), "bugs", lambda row: build_critter_facts(row, include_weather=True), {}),
        ("sea", "nh_sea_creature", lambda row: build_subtitle([row.get("shadow_size", ""), row.get("rarity", ""), format_bells(row.get("sell_nook"))]), "sea", lambda row: build_critter_facts(row, include_shadow=True, include_movement=True), {}),
    ]:
        fields = "name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,catchphrase3,location,weather,shadow_size,shadow_movement,rarity,sell_nook,sell_flick,sell_cj,time,n_availability,s_availability"
        rows = cargo_query_all(tables=table_name, fields=fields)
        for row in rows:
            facts = fact_builder(row)
            item_entry = make_item_entry(
                entry_id=stable_id("item", f"{section_id}:{row.get('name', '')}"),
                title=row.get("name", ""),
                dataset_id=item_dataset,
                page_title=row.get("page_title", "") or row.get("name", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
            )
            register_item_if_needed(item_entry, build_item_aliases(row.get("name", "")))
            add_section(
                section_id,
                make_section_entry(
                    entry_id=stable_id("section", f"{section_id}:{row.get('name', '')}"),
                    title=row.get("name", ""),
                    subtitle=subtitle_builder(row),
                    page_title=row.get("page_title", "") or row.get("name", ""),
                    image_url=row.get("image_url", ""),
                    facts_text=facts,
                    section_id=section_id,
                ),
            )

    fossil_rows = cargo_query_all(
        tables="nh_fossil",
        fields="name,_pageName=page_title,image_url,fossil_group,interactable,sell,color1,color2,width,length",
    )
    for row in fossil_rows:
        facts = build_fossil_facts(row)
        item_entry = make_item_entry(
            entry_id=stable_id("item", f"fossil:{row.get('name', '')}"),
            title=row.get("name", ""),
            dataset_id="fossils",
            page_title=row.get("page_title", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )
        register_item_if_needed(item_entry, build_item_aliases(row.get("name", "")))
        add_section(
            "fossils",
            make_section_entry(
                entry_id=stable_id("section", f"fossil:{row.get('name', '')}"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("fossil_group", ""), format_bells(row.get("sell"))]),
                page_title=row.get("page_title", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="fossils",
            ),
        )

    villager_rows = cargo_query_all(
        tables="villager,nh_villager",
        join_on="villager._pageName=nh_villager._pageName",
        where='villager.nh="1"',
        fields="villager.name=name,villager._pageName=page_title,villager.species,villager.personality,villager.gender,villager.birthday_month,villager.birthday_day,villager.quote,villager.phrase,nh_villager.icon_url=image_url,nh_villager.catchphrase=nh_catchphrase,nh_villager.hobby=nh_hobby,nh_villager.fav_style1=fav_style1,nh_villager.fav_style2=fav_style2,nh_villager.fav_color1=fav_color1,nh_villager.fav_color2=fav_color2",
    )
    for row in villager_rows:
        facts = build_villager_facts(row)
        birthday = " ".join(filter(None, [row.get("birthday_month"), row.get("birthday_day")]))
        add_section(
            "villagers",
            make_section_entry(
                entry_id=stable_id("section", f"villager:{row.get('name', '')}"),
                title=row.get("name", ""),
                subtitle=build_subtitle([row.get("species", ""), row.get("personality", ""), birthday]),
                page_title=row.get("page_title", "") or row.get("name", ""),
                image_url=row.get("image_url", ""),
                facts_text=facts,
                section_id="villagers",
            ),
        )

    current_year = datetime.now().year
    next_year = current_year + 1
    event_rows = cargo_query_all(
        tables="nh_calendar",
        fields="event,date,type,link=page_title",
        where=f'YEAR(date)="{current_year}" OR YEAR(date)="{next_year}"',
        order_by="date",
    )
    for row in event_rows:
        add_section(
            "events",
            make_section_entry(
                entry_id=stable_id("section", f"event:{row.get('event', '')}:{row.get('date', '')}"),
                title=row.get("event", ""),
                subtitle=build_subtitle([row.get("date", ""), row.get("type", "")]),
                page_title=row.get("page_title", ""),
                image_url="",
                facts_text=build_event_facts(row),
                section_id="events",
            ),
        )

    payload["item_entries"] = item_entries
    payload["item_lookup"] = {key: entry_id for key, (_, entry_id) in item_lookup.items()}
    payload["encyclopedia"] = encyclopedia
    refresh_payload_stats(payload, cache_dir)
    save_payload(cache_dir, payload)
    emit(progress, f"轻量离线百科已写入：{cache_dir / CACHE_FILE_NAME}")
    return payload


def cache_single_item_entry(
    *,
    english_name: str,
    cache_dir: Path,
    progress: ProgressCallback | None = None,
) -> dict[str, str] | None:
    payload = load_payload(cache_dir)
    item_entries = dict(payload.get("item_entries", {}))
    item_lookup = dict(payload.get("item_lookup", {}))
    for candidate in [normalize_text(english_name)]:
        entry_id = item_lookup.get(candidate)
        if entry_id and entry_id in item_entries:
            return item_entries[entry_id]

    raw_name = clean_text(english_name)
    if not raw_name or raw_name == "(None)":
        return None

    base_name = raw_name
    variation_token = ""
    variant_match = re.match(r"^(.*) \((.*)\)$", raw_name)
    if variant_match and not raw_name.endswith(" in.)"):
        base_name = variant_match.group(1).strip()
        variation_token = variant_match.group(2).strip()

    def simple_item(table: str, fields: str, dataset_id: str, facts_builder, *, name_field: str = "en_name", query_name: str | None = None) -> dict[str, str] | None:
        target_name = query_name or raw_name
        row = cargo_query_first(tables=table, fields=fields, where=f'{name_field}="{escape_cargo_value(target_name)}"')
        if not row:
            return None
        facts = facts_builder(row)
        return make_item_entry(
            entry_id=stable_id("item", f"{dataset_id}:{raw_name}"),
            title=raw_name,
            dataset_id=dataset_id,
            page_title=row.get("page_title", "") or row.get("name", ""),
            image_url=row.get("image_url", ""),
            facts_text=facts,
        )

    entry = simple_item("nh_item", "_pageName=page_title,en_name=name,image_url,stack,hha_base,buy1_price,sell,material_type,plant_type,availability1,availability1_note,availability2,availability2_note,unlocked,version_added,notes", "other_items", build_other_item_facts)
    if entry is None and raw_name.endswith(" (DIY recipe)"):
        entry = simple_item("nh_recipe", "_pageName=page_title,en_name=name,image_url,sell,recipes_to_unlock,diy_availability1,diy_availability1_note,diy_availability2,diy_availability2_note,material1,material1_num,material2,material2_num,material3,material3_num,material4,material4_num,material5,material5_num,material6,material6_num", "recipes", build_recipe_facts, query_name=base_name)
    if entry is None:
        entry = simple_item("nh_recipe", "_pageName=page_title,en_name=name,image_url,sell,recipes_to_unlock,diy_availability1,diy_availability1_note,diy_availability2,diy_availability2_note,material1,material1_num,material2,material2_num,material3,material3_num,material4,material4_num,material5,material5_num,material6,material6_num", "recipes", build_recipe_facts)
    if entry is None:
        art_query_name = raw_name[:-10] if raw_name.endswith(" (forgery)") else raw_name
        row = cargo_query_first(tables="nh_art", fields="_pageName=page_title,name,image_url,fake_image_url,art_name,art_type,author,year,art_style,sell,availability,authenticity", where=f'name="{escape_cargo_value(art_query_name)}"')
        if row:
            image_url = row.get("fake_image_url", "") if raw_name.endswith(" (forgery)") else row.get("image_url", "")
            entry = make_item_entry(
                entry_id=stable_id("item", f"art:{raw_name}"),
                title=raw_name,
                dataset_id="art",
                page_title=row.get("page_title", ""),
                image_url=image_url,
                facts_text=build_art_facts(row, fake_label="赝品图" if raw_name.endswith(" (forgery)") else ""),
            )

    if entry is None:
        entry = simple_item("nh_interior", "_pageName=page_title,en_name=name,image_url,category,item_series,item_set,theme1,theme2,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,color1,color2,unlocked,version_added,notes", "interior", build_interior_facts)

    def variation_item(base_table: str, base_fields: str, variation_table: str, variation_fields: str, dataset_id: str, fact_builder):
        row = cargo_query_first(tables=base_table, fields=base_fields, where=f'en_name="{escape_cargo_value(raw_name)}"')
        selected_row = None
        query_key = raw_name
        if not row and variation_token:
            row = cargo_query_first(tables=base_table, fields=base_fields, where=f'en_name="{escape_cargo_value(base_name)}"')
            query_key = base_name
        if not row:
            return None
        variations = cargo_query_all(tables=variation_table, fields=variation_fields, where=f'en_name="{escape_cargo_value(query_key)}"', order_by="variation_number")
        selected_row = match_variation_row(variations, variation_token) if variation_token else (variations[0] if variations else None)
        title = raw_name if variation_token else row.get("name", raw_name)
        return make_item_entry(
            entry_id=stable_id("item", f"{dataset_id}:{raw_name}"),
            title=title,
            dataset_id=dataset_id,
            page_title=row.get("page_title", ""),
            image_url=selected_row.get("image_url", "") if selected_row else "",
            facts_text=fact_builder(row, selected_row),
        )

    if entry is None:
        entry = variation_item("nh_clothing", "_pageName=page_title,en_name=name,category,style1,style2,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,seasonality,unlocked,version_added,notes", "nh_clothing_variation", "en_name=name,variation,image_url,color1,color2", "clothing", build_clothing_facts)
    if entry is None:
        entry = variation_item("nh_furniture", "_pageName=page_title,en_name=name,category,item_series,item_set,theme1,theme2,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,availability3,availability3_note,customizable,grid_size,height,unlocked,version_added,notes", "nh_furniture_variation", "en_name=name,variation,pattern,image_url,color1,color2", "furniture", build_furniture_facts)
    if entry is None:
        entry = variation_item("nh_tool", "_pageName=page_title,en_name=name,uses,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,availability3,availability3_note,customizable,unlocked,version_added,notes", "nh_tool_variation", "en_name=name,variation,image_url", "tools", build_tool_facts)
    if entry is None:
        entry = variation_item("nh_photo", "_pageName=page_title,en_name=name,category,buy1_price,sell,availability1,availability1_note,availability2,availability2_note,interactable,grid_size,unlocked,version_added", "nh_photo_variation", "en_name=name,variation,image_url,color1,color2", "photos", build_photo_facts)
    if entry is None:
        entry = variation_item("nh_gyroid", "_pageName=page_title,en_name=name,sell,cyrus_price,availability1,availability1_note,availability2,availability2_note,availability3,availability3_note,customizable,grid_size,sound,unlocked,version_added,notes", "nh_gyroid_variation", "en_name=name,variation,image_url,color1,color2", "gyroids", build_gyroid_facts)
    if entry is None:
        entry = simple_item("nh_fish", "name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,catchphrase3,location,shadow_size,rarity,sell_nook,time,n_availability,s_availability", "fish", lambda row: build_critter_facts(row, include_shadow=True), name_field="name")
    if entry is None:
        entry = simple_item("nh_bug", "name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,location,weather,rarity,sell_nook,sell_flick,time,n_availability,s_availability", "bugs", lambda row: build_critter_facts(row, include_weather=True), name_field="name")
    if entry is None:
        entry = simple_item("nh_sea_creature", "name,_pageName=page_title,image_url,number,catchphrase,catchphrase2,shadow_size,shadow_movement,rarity,sell_nook,time,n_availability,s_availability", "sea", lambda row: build_critter_facts(row, include_shadow=True, include_movement=True), name_field="name")
    if entry is None:
        entry = simple_item("nh_fossil", "name,_pageName=page_title,image_url,fossil_group,interactable,sell,color1,color2,width,length", "fossils", build_fossil_facts, name_field="name")

    if entry is None:
        return None

    enrich_entries_with_assets([entry], cache_dir, download_images_enabled=True, progress=progress)
    item_entries[entry["id"]] = entry
    for candidate in [normalize_text(raw_name)]:
        item_lookup[candidate] = entry["id"]
    payload["item_entries"] = item_entries
    payload["item_lookup"] = item_lookup
    refresh_payload_stats(payload, cache_dir)
    save_payload(cache_dir, payload)
    return entry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同步 Nookipedia 离线资料")
    parser.add_argument("--csv", required=True, help="items.csv 路径")
    parser.add_argument("--cache-dir", required=True, help="离线缓存目录")
    parser.add_argument("--skip-images", action="store_true", help="只同步文字资料，不下载图片")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    def progress(message: str) -> None:
        print(message)

    result = sync_helper_cache(
        csv_path=Path(args.csv).expanduser(),
        cache_dir=Path(args.cache_dir).expanduser(),
        download_images_enabled=not args.skip_images,
        progress=progress,
    )
    print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
