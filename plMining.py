"""plMining - Pattern Library Mining for Animal Crossing Offline Assistant.

Provides lightweight analysis utilities that run SQL aggregations against the
local ``pattern_entries`` table and surface structured statistics without
loading every row into Python memory.

Typical usage
-------------
Run directly from the project root to print a full JSON report::

    py -3.11 plMining.py
    py -3.11 plMining.py --db path/to/animal_crossing_offline.db

Or import as a library::

    from pathlib import Path
    from plMining import PatternLibraryMiner

    miner = PatternLibraryMiner(Path("data/animal_crossing_offline.db"))
    summary = miner.mine_collection_summary()
    print(summary)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CreatorStat:
    """Aggregated statistics for a single pattern creator."""
    creator: str
    total: int
    saved: int


@dataclass(slots=True)
class TypeStat:
    """Aggregated statistics for a single pattern type."""
    pattern_type: str
    total: int
    saved: int


@dataclass(slots=True)
class TagStat:
    """Frequency count for a single tag token."""
    tag: str
    count: int


@dataclass(slots=True)
class CollectionSummary:
    """Overall statistics for the local pattern collection."""
    total_patterns: int
    saved_patterns: int
    unique_creators: int
    unique_types: int
    unique_tags: int
    source_counts: dict[str, int]
    top_creators: list[CreatorStat]
    top_types: list[TypeStat]
    top_tags: list[TagStat]


# ---------------------------------------------------------------------------
# Miner
# ---------------------------------------------------------------------------

class PatternLibraryMiner:
    """Mines the ``pattern_entries`` table for aggregated statistics.

    Parameters
    ----------
    db_path:
        Path to the SQLite database that stores ``pattern_entries``.
    top_n:
        How many rows to return for each "top-N" ranking query.
        Defaults to ``20``.
    """

    def __init__(self, db_path: Path, *, top_n: int = 20) -> None:
        self.db_path = db_path
        self.top_n = top_n

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _table_exists(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pattern_entries'"
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Public mining methods
    # ------------------------------------------------------------------

    def mine_creator_stats(self) -> list[CreatorStat]:
        """Return one :class:`CreatorStat` per creator, sorted by *total* descending.

        Empty or blank creator values are grouped under an empty string.
        """
        with self._connect() as connection:
            if not self._table_exists(connection):
                return []
            rows = connection.execute(
                """
                SELECT
                    TRIM(COALESCE(creator, '')) AS creator,
                    COUNT(*)                    AS total,
                    SUM(is_saved)               AS saved
                FROM pattern_entries
                GROUP BY TRIM(COALESCE(creator, ''))
                ORDER BY total DESC, creator COLLATE NOCASE
                LIMIT ?
                """,
                (self.top_n,),
            ).fetchall()
        return [CreatorStat(creator=row["creator"], total=row["total"], saved=row["saved"] or 0) for row in rows]

    def mine_type_stats(self) -> list[TypeStat]:
        """Return one :class:`TypeStat` per pattern type, sorted by *total* descending."""
        with self._connect() as connection:
            if not self._table_exists(connection):
                return []
            rows = connection.execute(
                """
                SELECT
                    TRIM(COALESCE(pattern_type, '')) AS pattern_type,
                    COUNT(*)                         AS total,
                    SUM(is_saved)                    AS saved
                FROM pattern_entries
                GROUP BY TRIM(COALESCE(pattern_type, ''))
                ORDER BY total DESC, pattern_type COLLATE NOCASE
                LIMIT ?
                """,
                (self.top_n,),
            ).fetchall()
        return [TypeStat(pattern_type=row["pattern_type"], total=row["total"], saved=row["saved"] or 0) for row in rows]

    def _count_all_tags(self) -> tuple[dict[str, int], int]:
        """Return a ``(counts_dict, unique_count)`` tuple covering *all* tag tokens.

        This is a private helper used by both :meth:`mine_tag_stats` and
        :meth:`mine_collection_summary` so the full unique-tag count is always
        accurate regardless of *top_n*.
        """
        with self._connect() as connection:
            if not self._table_exists(connection):
                return {}, 0
            rows = connection.execute("SELECT tags FROM pattern_entries WHERE tags IS NOT NULL AND tags != ''").fetchall()

        counts: dict[str, int] = {}
        for row in rows:
            for token in row["tags"].split(","):
                token = token.strip()
                if token:
                    counts[token] = counts.get(token, 0) + 1
        return counts, len(counts)

    def mine_tag_stats(self) -> list[TagStat]:
        """Return one :class:`TagStat` per unique tag token, sorted by *count* descending.

        Tags are stored as comma-separated strings; this method splits each row
        on ``','`` and counts individual tokens after stripping whitespace.
        Only the top *top_n* entries are returned; use
        :meth:`mine_collection_summary` to obtain the true total unique-tag count.
        """
        counts, _ = self._count_all_tags()
        sorted_entries = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [TagStat(tag=tag, count=count) for tag, count in sorted_entries[: self.top_n]]

    def mine_source_counts(self) -> dict[str, int]:
        """Return a mapping of *source_type* → count."""
        with self._connect() as connection:
            if not self._table_exists(connection):
                return {}
            rows = connection.execute(
                """
                SELECT
                    COALESCE(source_type, '') AS source_type,
                    COUNT(*)                  AS total
                FROM pattern_entries
                GROUP BY COALESCE(source_type, '')
                ORDER BY total DESC
                """
            ).fetchall()
        return {row["source_type"]: row["total"] for row in rows}

    def mine_collection_summary(self) -> CollectionSummary:
        """Return a complete :class:`CollectionSummary` for the pattern collection."""
        with self._connect() as connection:
            if not self._table_exists(connection):
                return CollectionSummary(
                    total_patterns=0,
                    saved_patterns=0,
                    unique_creators=0,
                    unique_types=0,
                    unique_tags=0,
                    source_counts={},
                    top_creators=[],
                    top_types=[],
                    top_tags=[],
                )
            row = connection.execute(
                """
                SELECT
                    COUNT(*)                                                        AS total_patterns,
                    SUM(is_saved)                                                   AS saved_patterns,
                    COUNT(DISTINCT TRIM(COALESCE(creator, '')))                     AS unique_creators,
                    COUNT(DISTINCT TRIM(COALESCE(pattern_type, '')))                AS unique_types
                FROM pattern_entries
                """
            ).fetchone()

        _, unique_tag_count = self._count_all_tags()
        return CollectionSummary(
            total_patterns=row["total_patterns"] or 0,
            saved_patterns=row["saved_patterns"] or 0,
            unique_creators=row["unique_creators"] or 0,
            unique_types=row["unique_types"] or 0,
            unique_tags=unique_tag_count,
            source_counts=self.mine_source_counts(),
            top_creators=self.mine_creator_stats(),
            top_types=self.mine_type_stats(),
            top_tags=self.mine_tag_stats(),
        )


# ---------------------------------------------------------------------------
# Serialisation helper
# ---------------------------------------------------------------------------

def _serialise(obj: object) -> object:
    """Recursively convert dataclasses and lists to plain dicts/lists."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialise(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_serialise(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_default_db_path(db_arg: str | None) -> Path | None:
    if db_arg:
        return Path(db_arg)
    candidates = [
        Path(__file__).parent / "data" / "animal_crossing_offline.db",
        Path("data") / "animal_crossing_offline.db",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="plMining - Pattern Library Mining for Animal Crossing Offline Assistant"
    )
    parser.add_argument("--db", help="Path to the SQLite database (default: data/animal_crossing_offline.db)")
    parser.add_argument("--top", type=int, default=20, metavar="N", help="Number of rows to show per ranking (default: 20)")
    parser.add_argument(
        "--report",
        choices=["summary", "creators", "types", "tags"],
        default="summary",
        help="Which report to generate (default: summary)",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    db_path = _resolve_default_db_path(args.db)
    if db_path is None or not db_path.exists():
        print(json.dumps({"error": "Database not found. Use --db to specify a path."}, ensure_ascii=False))
        return 1

    miner = PatternLibraryMiner(db_path, top_n=args.top)

    if args.report == "summary":
        result = _serialise(miner.mine_collection_summary())
    elif args.report == "creators":
        result = _serialise(miner.mine_creator_stats())
    elif args.report == "types":
        result = _serialise(miner.mine_type_stats())
    elif args.report == "tags":
        result = _serialise(miner.mine_tag_stats())
    else:
        result = {}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
