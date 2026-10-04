"""SQLite persistence for user-owned data."""

import json
import math
import sqlite3
from contextlib import closing, nullcontext
from dataclasses import asdict, replace
from datetime import datetime,timedelta,timezone
from pathlib import Path

from .ai_types import (AIProgress, CandidateBatch, InsightText, ProjectExplanation,
                       RelevanceVerdict)
from .daily_update import (AutoAttemptState, AutoUpdateSettings, valid_update_time)
from .models import GrowthCoverage, KeywordRule, Recommendation, Repository, StarSnapshot
from .discovery_types import (DiscoveryBatch, DiscoveryCandidate, SourceEvidence,
                              valid_repository_name)
from .cross_check import CrossCheck
from .readme_types import ReadmeDocument, EXTRACTION_VERSION
from .translation_types import TranslationPart
from .legacy_growth import classify_legacy_growth, validate_growth_repair
import hashlib


class RadarStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "radar.db"
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY,
                    term TEXT NOT NULL,
                    term_key TEXT NOT NULL UNIQUE,
                    min_stars INTEGER NOT NULL CHECK (min_stars >= 0),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS readme_cache (
                    repo_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL,
                    text TEXT NOT NULL, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
                    etag TEXT, content_hash TEXT NOT NULL, truncated INTEGER NOT NULL,
                    extraction_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS translation_cache (
                    cache_key TEXT PRIMARY KEY, parts TEXT NOT NULL, saved_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_translation_cache_saved
                    ON translation_cache(saved_at, cache_key);
                CREATE TABLE IF NOT EXISTS repositories (
                    id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    html_url TEXT NOT NULL,
                    description TEXT NOT NULL,
                    topics TEXT NOT NULL,
                    language TEXT,
                    stars INTEGER NOT NULL,
                    archived INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS follows (
                    repo_id INTEGER PRIMARY KEY REFERENCES repositories(id),
                    followed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS follow_folders (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS follow_folder_sequence (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    last_id INTEGER NOT NULL CHECK(last_id>=0)
                );
                INSERT INTO follow_folder_sequence(singleton,last_id)
                  VALUES(1,COALESCE((SELECT MAX(id) FROM follow_folders),0))
                  ON CONFLICT(singleton) DO UPDATE SET last_id=MAX(last_id,excluded.last_id);
                CREATE TABLE IF NOT EXISTS follow_folder_items (
                    folder_id INTEGER NOT NULL REFERENCES follow_folders(id) ON DELETE CASCADE,
                    repo_id INTEGER NOT NULL REFERENCES follows(repo_id) ON DELETE CASCADE,
                    PRIMARY KEY (folder_id, repo_id)
                );
                CREATE INDEX IF NOT EXISTS idx_follow_folder_repo ON follow_folder_items(repo_id);
                CREATE TABLE IF NOT EXISTS snapshots (
                    repo_id INTEGER NOT NULL REFERENCES repositories(id),
                    local_date TEXT NOT NULL,
                    stars INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (repo_id, local_date)
                );
                CREATE TABLE IF NOT EXISTS recommendations (
                    repo_id INTEGER NOT NULL REFERENCES repositories(id),
                    local_date TEXT NOT NULL,
                    section TEXT NOT NULL,
                    keyword_id INTEGER,
                    star_delta INTEGER,
                    baseline_at TEXT,
                    observed_at TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (local_date, repo_id)
                );
                CREATE INDEX IF NOT EXISTS idx_recommendations_date
                    ON recommendations(local_date);
                CREATE TABLE IF NOT EXISTS daily_runs (
                    local_date TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS section_runs (
                    local_date TEXT NOT NULL REFERENCES daily_runs(local_date),
                    section TEXT NOT NULL,
                    keyword_id INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (local_date, section, keyword_id)
                );
                CREATE TABLE IF NOT EXISTS growth_runs (
                    local_date TEXT PRIMARY KEY REFERENCES daily_runs(local_date),
                    candidate_count INTEGER NOT NULL,
                    scored_count INTEGER NOT NULL,
                    source_names TEXT NOT NULL,
                    stat_date TEXT,
                    metric_basis TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS refresh_failure (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    attempted_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_cross_checks (
                    local_date TEXT NOT NULL, repo_id INTEGER NOT NULL,
                    metric TEXT NOT NULL, source_key TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(local_date,repo_id,metric,source_key)
                );
                CREATE TABLE IF NOT EXISTS official_star_evidence (
                    repo_id INTEGER NOT NULL, observed_at TEXT NOT NULL,
                    week INTEGER NOT NULL, day_index INTEGER NOT NULL,
                    added INTEGER NOT NULL, interpretation_rule TEXT NOT NULL,
                    PRIMARY KEY(repo_id,observed_at,week,day_index)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auto_update_runs (
                    local_date TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL CHECK (attempts BETWEEN 0 AND 3),
                    last_attempt_at TEXT,
                    status TEXT,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS ai_keyword_progress (
                    local_date TEXT NOT NULL,
                    keyword_id INTEGER NOT NULL,
                    model_key TEXT NOT NULL,
                    next_page INTEGER NOT NULL,
                    next_offset INTEGER NOT NULL,
                    checked_count INTEGER NOT NULL,
                    PRIMARY KEY(local_date, keyword_id, model_key)
                );
                CREATE TABLE IF NOT EXISTS ai_verdicts (
                    local_date TEXT NOT NULL,
                    keyword_id INTEGER NOT NULL,
                    model_key TEXT NOT NULL,
                    repo_id INTEGER NOT NULL,
                    verdict TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    source_limited INTEGER NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY(local_date, keyword_id, model_key, repo_id)
                );
                CREATE TABLE IF NOT EXISTS ai_removed_recommendations (
                    local_date TEXT NOT NULL,
                    keyword_id INTEGER NOT NULL,
                    repo_id INTEGER NOT NULL,
                    removed_at TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    PRIMARY KEY(local_date, keyword_id, repo_id)
                );
                CREATE TABLE IF NOT EXISTS ai_explanations (
                    repo_id INTEGER NOT NULL,
                    keyword_key INTEGER NOT NULL,
                    model_key TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    content TEXT NOT NULL,
                    saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(repo_id, keyword_key, model_key, source_hash)
                );
                CREATE TABLE IF NOT EXISTS discovery_catalog (
                    catalog_key TEXT PRIMARY KEY,
                    repo_id INTEGER,
                    full_name TEXT NOT NULL,
                    source_names TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    last_discovered_at TEXT NOT NULL,
                    last_scored_at TEXT,
                    activity_hint INTEGER,
                    cached_repo TEXT
                );
                CREATE TABLE IF NOT EXISTS discovery_cursors (
                    source_name TEXT PRIMARY KEY,
                    cursor TEXT,
                    batch_finished INTEGER NOT NULL,
                    notes TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_evidence (
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    repo_id INTEGER,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(source_name, source_url, full_name, observed_at)
                );
                CREATE INDEX IF NOT EXISTS idx_source_evidence_repo
                    ON source_evidence(repo_id, observed_at);
                """
            )
            folder_columns={row['name'] for row in connection.execute('PRAGMA table_info(follow_folders)')}
            verdict_columns={row['name'] for row in connection.execute('PRAGMA table_info(ai_verdicts)')}
            if 'candidate_rank' not in verdict_columns:
                connection.execute('ALTER TABLE ai_verdicts ADD COLUMN candidate_rank INTEGER')
            if 'sort_position' not in folder_columns:
                connection.execute('ALTER TABLE follow_folders ADD COLUMN sort_position INTEGER NOT NULL DEFAULT 0')
                legacy=[row[0] for row in connection.execute('SELECT id FROM follow_folders ORDER BY name_key,id')]
                connection.executemany('UPDATE follow_folders SET sort_position=? WHERE id=?',enumerate(legacy))
            keyword_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(keywords)")
            }
            if "deleted_at" not in keyword_columns:
                connection.execute("ALTER TABLE keywords ADD COLUMN deleted_at TEXT")
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(recommendations)").fetchall()
            }
            if "position" not in columns:
                connection.execute("ALTER TABLE recommendations ADD COLUMN position INTEGER")
                connection.execute(
                    """WITH ordered AS (
                        SELECT rec.repo_id,
                            ROW_NUMBER() OVER (
                                PARTITION BY rec.local_date
                                ORDER BY CASE rec.section WHEN 'growth' THEN 0 ELSE 1 END,
                                    COALESCE(rec.keyword_id, 0),
                                    COALESCE(rec.star_delta, 0) DESC,
                                    repo.stars DESC, rec.repo_id
                            ) AS new_position
                        FROM recommendations AS rec
                        LEFT JOIN repositories AS repo ON repo.id = rec.repo_id
                    )
                    UPDATE recommendations SET position = (
                        SELECT new_position FROM ordered
                        WHERE ordered.repo_id = recommendations.repo_id
                    ) WHERE position IS NULL"""
                )
            if "metric_basis" not in columns:
                connection.execute("ALTER TABLE recommendations ADD COLUMN metric_basis TEXT")
            if "metric_date" not in columns:
                connection.execute("ALTER TABLE recommendations ADD COLUMN metric_date TEXT")
            self._migrate_recommendation_key(connection)
            columns = {row["name"] for row in
                       connection.execute("PRAGMA table_info(recommendations)")}
            for name, definition in (("rank", "INTEGER"), ("display_role", "TEXT"),
                                     ("matched_keyword_ids", "TEXT NOT NULL DEFAULT '[]'")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE recommendations ADD COLUMN {name} {definition}")
            coverage_columns = {row[1] for row in connection.execute("PRAGMA table_info(growth_runs)")}
            for name, definition in (("catalog_count", "INTEGER"), ("failed_count", "INTEGER NOT NULL DEFAULT 0"),
                                      ("stop_reasons", "TEXT NOT NULL DEFAULT '[]'")):
                if name not in coverage_columns:
                    connection.execute(f"ALTER TABLE growth_runs ADD COLUMN {name} {definition}")
            connection.execute(
                """INSERT OR IGNORE INTO daily_runs(local_date, completed_at)
                SELECT rec.local_date,
                    COALESCE(
                        (SELECT MAX(s.observed_at) FROM snapshots AS s
                         WHERE s.local_date = rec.local_date),
                        MAX(rec.observed_at)
                    )
                FROM recommendations AS rec GROUP BY rec.local_date"""
            )
            connection.execute(
                """INSERT OR IGNORE INTO section_runs(local_date, section, keyword_id)
                SELECT DISTINCT local_date, section, COALESCE(keyword_id, 0)
                FROM recommendations"""
            )
            from .search_storage import initialize_search_schema
            initialize_search_schema(connection)
            # Visibility survives replacing a current-day ranking. Discovery alone
            # never fires these triggers and therefore cannot consume future slots.
            connection.execute("""CREATE TABLE IF NOT EXISTS displayed_repositories (
                repo_id INTEGER NOT NULL, local_date TEXT NOT NULL,
                PRIMARY KEY(repo_id,local_date))""")
            connection.execute("INSERT OR IGNORE INTO displayed_repositories SELECT repo_id,local_date FROM recommendations")
            connection.execute("INSERT OR IGNORE INTO displayed_repositories SELECT repo_id,local_date FROM ai_removed_recommendations")
            connection.execute("""CREATE TRIGGER IF NOT EXISTS record_visible_insert
                AFTER INSERT ON recommendations BEGIN
                INSERT OR IGNORE INTO displayed_repositories VALUES(NEW.repo_id,NEW.local_date); END""")
            connection.execute("""CREATE TRIGGER IF NOT EXISTS record_visible_delete
                BEFORE DELETE ON recommendations BEGIN
                INSERT OR IGNORE INTO displayed_repositories VALUES(OLD.repo_id,OLD.local_date); END""")
            self._migrate_legacy_growth(connection)

    @staticmethod
    def _seen_before(connection: sqlite3.Connection, local_date: str) -> set[int]:
        return {row['repo_id'] for row in connection.execute(
            'SELECT repo_id FROM recommendations WHERE local_date<? UNION '
            'SELECT repo_id FROM ai_removed_recommendations WHERE local_date<? UNION '
            'SELECT repo_id FROM displayed_repositories WHERE local_date<?',
            (local_date, local_date, local_date))}

    def seen_repo_ids_before(self, local_date: str) -> set[int]:
        with closing(self._connect()) as connection:
            return self._seen_before(connection, local_date)

    def growth_repair_pending(self, local_date: str) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute('SELECT 1 FROM legacy_growth_repairs WHERE local_date=?',
                                      (local_date,)).fetchone() is not None

    def growth_reserved_ids(self, local_date: str) -> set[int]:
        with closing(self._connect()) as connection:
            return self._growth_reserved_ids(connection, local_date)

    @staticmethod
    def _growth_reserved_ids(connection: sqlite3.Connection, local_date: str) -> set[int]:
        return {r['repo_id'] for r in connection.execute(
            "SELECT repo_id FROM recommendations WHERE local_date=? AND section!='growth' "
            'UNION SELECT repo_id FROM ai_removed_recommendations WHERE local_date=?',
            (local_date, local_date))}

    @staticmethod
    def _archive_growth(connection: sqlite3.Connection, row: sqlite3.Row,
                        record_kind: str) -> None:
        connection.execute('''INSERT OR IGNORE INTO legacy_growth_archive
            (local_date,repo_id,record_kind,payload,archived_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)''',
            (row['local_date'], row['repo_id'], record_kind,
             json.dumps(dict(row), ensure_ascii=False)))

    def _migrate_legacy_growth(self, connection: sqlite3.Connection) -> None:
        connection.execute('''CREATE TABLE IF NOT EXISTS legacy_growth_archive (
            local_date TEXT NOT NULL, repo_id INTEGER NOT NULL, record_kind TEXT NOT NULL,
            payload TEXT NOT NULL, archived_at TEXT NOT NULL,
            PRIMARY KEY(local_date,repo_id,record_kind))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS legacy_growth_repairs (
            local_date TEXT PRIMARY KEY)''')
        # Null roles with official metrics identify the affected old ranked issues.
        # Unmeasured snapshot rows do not acquire invented growth ranks.
        rows = connection.execute('''SELECT * FROM recommendations WHERE section='growth'
            AND display_role IS NULL AND metric_basis='github_daily_new'
            ORDER BY local_date,position''').fetchall()
        latest = connection.execute('SELECT MAX(local_date) FROM recommendations').fetchone()[0]
        dates = sorted({row['local_date'] for row in rows})
        for local_date in dates:
            legacy = [row for row in rows if row['local_date'] == local_date]
            seen = self._seen_before(connection, local_date)
            classified = classify_legacy_growth([self._recommendation(r) for r in legacy], seen)
            for original, updated in zip(legacy, classified):
                self._archive_growth(connection, original, 'recommendation')
                known_rank = (original['position'] if original['star_delta'] is not None
                              and original['metric_date'] else None)
                connection.execute('''UPDATE recommendations SET display_role=?,rank=COALESCE(rank,?)
                    WHERE local_date=? AND repo_id=?''',
                    (updated.display_role, known_rank, local_date, updated.repo_id))
            current = connection.execute('''SELECT display_role FROM recommendations
                WHERE local_date=? AND section='growth' ''', (local_date,)).fetchall()
            if (local_date == latest and any(r['display_role'] == 'old' for r in current)
                    and sum(r['display_role'] == 'new' for r in current) < 5):
                connection.execute('INSERT OR IGNORE INTO legacy_growth_repairs VALUES (?)',
                                   (local_date,))

    @staticmethod
    def _migrate_recommendation_key(connection: sqlite3.Connection) -> None:
        key = {row["name"]: row["pk"] for row in
               connection.execute("PRAGMA table_info(recommendations)").fetchall()}
        if key.get("local_date") == 1 and key.get("repo_id") == 2:
            connection.execute("PRAGMA user_version=5")
            return
        connection.execute("""CREATE TABLE recommendations_migrated (
                repo_id INTEGER NOT NULL REFERENCES repositories(id),
                local_date TEXT NOT NULL,
                section TEXT NOT NULL,
                keyword_id INTEGER,
                star_delta INTEGER,
                baseline_at TEXT,
                observed_at TEXT NOT NULL,
                position INTEGER NOT NULL,
                metric_basis TEXT,
                metric_date TEXT,
                PRIMARY KEY (local_date, repo_id)
            )""")
        connection.execute("""INSERT INTO recommendations_migrated
                (repo_id, local_date, section, keyword_id, star_delta, baseline_at,
                 observed_at, position, metric_basis, metric_date)
                SELECT repo_id, local_date, section, keyword_id, star_delta, baseline_at,
                       observed_at, position, metric_basis, metric_date
                FROM recommendations""")
        connection.execute("DROP TABLE recommendations")
        connection.execute("ALTER TABLE recommendations_migrated RENAME TO recommendations")
        connection.execute("CREATE INDEX idx_recommendations_date ON recommendations(local_date)")
        connection.execute("PRAGMA user_version=5")

    def save_readme(self, document: ReadmeDocument) -> None:
        if (not valid_repository_name(document.full_name)
                or hashlib.sha256(document.text.encode('utf-8')).hexdigest() != document.content_hash):
            raise ValueError('README 缓存内容或仓库标识无效')
        with closing(self._connect()) as connection, connection:
            connection.execute('''INSERT INTO readme_cache VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(repo_id) DO UPDATE SET full_name=excluded.full_name,
                text=excluded.text, source_url=excluded.source_url, observed_at=excluded.observed_at,
                etag=excluded.etag, content_hash=excluded.content_hash,
                truncated=excluded.truncated, extraction_version=excluded.extraction_version''',
                (document.repo_id, document.full_name, document.text, document.source_url,
                 document.observed_at, document.etag, document.content_hash,
                 int(document.truncated), EXTRACTION_VERSION))

    def load_readme(self, repo_id: int) -> ReadmeDocument | None:
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT * FROM readme_cache WHERE repo_id=?', (repo_id,)).fetchone()
        if row is None: return None
        if (not valid_repository_name(row['full_name'])
                or hashlib.sha256(row['text'].encode('utf-8')).hexdigest() != row['content_hash']):
            raise ValueError('README 本地缓存损坏，请重新读取')
        return ReadmeDocument(row['repo_id'], row['full_name'], row['text'], row['source_url'],
                              row['observed_at'], row['etag'], row['content_hash'], bool(row['truncated']))

    def load_translation(self, key: str) -> tuple[TranslationPart, ...] | None:
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA busy_timeout=100')
            row = connection.execute('SELECT parts FROM translation_cache WHERE cache_key=?',
                                     (key,)).fetchone()
        if row is None:
            return None
        try:
            values = json.loads(row['parts'])
            if not isinstance(values, list) or any(
                    not isinstance(p, dict) or set(p) != {'kind', 'text'}
                    or p['kind'] not in ('text', 'literal') or not isinstance(p['text'], str)
                    for p in values):
                return None
            return tuple(TranslationPart(**p) for p in values)
        except (ValueError, TypeError):
            return None

    def save_translation(self, key: str, parts: tuple[TranslationPart, ...],
                         saved_at: str) -> None:
        payload = json.dumps([asdict(p) for p in parts], ensure_ascii=False)
        with closing(self._connect()) as connection, connection:
            connection.execute('PRAGMA busy_timeout=100')
            connection.execute('INSERT OR REPLACE INTO translation_cache VALUES (?, ?, ?)',
                               (key, payload, saved_at))
            extra = connection.execute('SELECT COUNT(*) FROM translation_cache').fetchone()[0] - 20000
            if extra > 0:
                connection.execute('DELETE FROM translation_cache WHERE cache_key IN '
                                   '(SELECT cache_key FROM translation_cache '
                                   'ORDER BY saved_at, cache_key LIMIT ?)', (extra,))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def save_discovery_batch(self, batch: DiscoveryBatch) -> None:
        """Save a resumable batch without adding anything to recommendation history."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for item in batch.candidates:
                if (not valid_repository_name(item.full_name) or
                        item.repo_id is not None and
                        (isinstance(item.repo_id, bool) or not isinstance(item.repo_id, int)
                         or item.repo_id <= 0)):
                    raise ValueError("发现的仓库身份无效")
                pending_key = "name:" + item.full_name.casefold()
                key = f"id:{item.repo_id}" if item.repo_id is not None else pending_key
                previous = connection.execute("SELECT * FROM discovery_catalog WHERE catalog_key=?",
                                              (key,)).fetchone()
                pending = (connection.execute("SELECT * FROM discovery_catalog WHERE catalog_key=?",
                                               (pending_key,)).fetchone()
                           if key != pending_key else None)
                existing = [row for row in (previous, pending) if row is not None]
                sources = set(item.source_names)
                first = item.discovered_at
                cached = json.dumps(asdict(item.cached_repo)) if item.cached_repo is not None else None
                hint = item.activity_hint
                for row in existing:
                    sources.update(json.loads(row["source_names"]))
                    first = min(first, row["discovered_at"])
                    if cached is None:
                        cached = row["cached_repo"]
                    if hint is None:
                        hint = row["activity_hint"]
                connection.execute("""INSERT INTO discovery_catalog
                    (catalog_key,repo_id,full_name,source_names,discovered_at,last_discovered_at,
                     activity_hint,cached_repo) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(catalog_key) DO UPDATE SET
                    full_name=excluded.full_name, source_names=excluded.source_names,
                    discovered_at=excluded.discovered_at,last_discovered_at=excluded.last_discovered_at,
                    activity_hint=excluded.activity_hint,cached_repo=excluded.cached_repo""",
                    (key, item.repo_id, item.full_name, json.dumps(sorted(sources)), first,
                     item.discovered_at, hint, cached))
                if pending is not None:
                    connection.execute("DELETE FROM discovery_catalog WHERE catalog_key=?", (pending_key,))
                for evidence in item.evidence:
                    self._write_source_evidence(connection, replace(evidence, repo_id=item.repo_id))
            connection.execute("""INSERT INTO discovery_cursors VALUES (?,?,?,?)
                ON CONFLICT(source_name) DO UPDATE SET cursor=excluded.cursor,
                batch_finished=excluded.batch_finished,notes=excluded.notes""",
                (batch.source_name, batch.cursor, int(batch.batch_finished), json.dumps(batch.notes)))

    def load_discovery_cursor(self, source_name: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT cursor FROM discovery_cursors WHERE source_name=?",
                                     (source_name,)).fetchone()
        return row["cursor"] if row is not None else None

    def catalog_candidates(self, limit: int) -> list[DiscoveryCandidate]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("候选数量无效")
        with closing(self._connect()) as connection:
            rows = connection.execute("""SELECT * FROM discovery_catalog
                ORDER BY last_scored_at ASC,discovered_at ASC,catalog_key LIMIT ?""", (limit,)).fetchall()
        result = []
        for row in rows:
            cached = json.loads(row["cached_repo"]) if row["cached_repo"] else None
            if cached is not None:
                cached["topics"] = tuple(cached["topics"])
            result.append(DiscoveryCandidate(row["full_name"], row["repo_id"],
                tuple(json.loads(row["source_names"])), row["discovered_at"], row["activity_hint"],
                Repository(**cached) if cached is not None else None,
                tuple(self.source_evidence(row["repo_id"])) if row["repo_id"] is not None else ()))
        return result

    @staticmethod
    def _write_source_evidence(connection: sqlite3.Connection, evidence: SourceEvidence) -> None:
        if not valid_repository_name(evidence.full_name):
            raise ValueError("来源仓库名称无效")
        connection.execute("""INSERT INTO source_evidence VALUES (?,?,?,?,?,?)
            ON CONFLICT(source_name,source_url,full_name,observed_at)
            DO UPDATE SET repo_id=excluded.repo_id,payload=excluded.payload""",
            (evidence.source_name, evidence.source_url, evidence.full_name, evidence.observed_at,
             evidence.repo_id, json.dumps(asdict(evidence), ensure_ascii=False)))

    def save_source_evidence(self, evidence: SourceEvidence) -> None:
        with closing(self._connect()) as connection, connection:
            self._write_source_evidence(connection, evidence)

    def source_evidence(self, repo_id: int) -> list[SourceEvidence]:
        with closing(self._connect()) as connection:
            rows = connection.execute("""SELECT payload FROM source_evidence WHERE repo_id=?
                ORDER BY observed_at,source_name,source_url""", (repo_id,)).fetchall()
        result = []
        for row in rows:
            content = json.loads(row["payload"])
            content["topics"] = tuple(content["topics"])
            result.append(SourceEvidence(**content))
        return result

    def add_keyword(self, term: str, min_stars: int = 1000) -> KeywordRule:
        clean = " ".join(term.split())
        if not clean:
            raise ValueError("关键词不能为空")
        if type(min_stars) is not int or min_stars < 0:
            raise ValueError("最低 Star 必须是非负整数")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO keywords(term, term_key, min_stars) VALUES (?, ?, ?)",
                (clean, clean.casefold(), min_stars),
            )
            row = connection.execute(
                "SELECT * FROM keywords WHERE term_key=?", (clean.casefold(),)
            ).fetchone()
            if row["deleted_at"] is not None:
                connection.execute(
                    "UPDATE keywords SET deleted_at=NULL, enabled=1 WHERE id=?",
                    (row["id"],),
                )
                row = connection.execute("SELECT * FROM keywords WHERE id=?", (row["id"],)).fetchone()
        return self._keyword(row)

    def list_keywords(self) -> list[KeywordRule]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM keywords WHERE deleted_at IS NULL ORDER BY id"
            ).fetchall()
        return [self._keyword(row) for row in rows]

    def all_keywords(self) -> list[KeywordRule]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM keywords ORDER BY id").fetchall()
        return [self._keyword(row) for row in rows]

    def set_keyword_enabled(self, keyword_id: int, enabled: bool) -> KeywordRule:
        if type(enabled) is not bool:
            raise ValueError("开启状态无效")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM keywords WHERE id=? AND deleted_at IS NULL", (keyword_id,)
            ).fetchone()
            if row is None:
                raise ValueError("关键词不存在")
            connection.execute("UPDATE keywords SET enabled=? WHERE id=?",
                               (int(enabled), keyword_id))
            row = connection.execute("SELECT * FROM keywords WHERE id=?", (keyword_id,)).fetchone()
        return self._keyword(row)

    def set_keyword_min_stars(self, keyword_id: int, min_stars: int) -> KeywordRule:
        if type(min_stars) is not int or min_stars < 0:
            raise ValueError("最低 Star 必须是非负整数")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM keywords WHERE id=? AND deleted_at IS NULL", (keyword_id,)
            ).fetchone()
            if row is None:
                raise ValueError("关键词不存在")
            connection.execute("UPDATE keywords SET min_stars=? WHERE id=?",
                               (min_stars, keyword_id))
            row = connection.execute("SELECT * FROM keywords WHERE id=?", (keyword_id,)).fetchone()
        return self._keyword(row)

    def soft_delete_keyword(self, keyword_id: int, at: str) -> None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT id FROM keywords WHERE id=?", (keyword_id,)).fetchone()
            if row is None:
                raise ValueError("关键词不存在")
            connection.execute(
                "UPDATE keywords SET deleted_at=COALESCE(deleted_at, ?), enabled=0 WHERE id=?",
                (at, keyword_id),
            )

    def tracked_repositories(self, limit: int = 20) -> list[Repository]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM repositories ORDER BY stars DESC, id LIMIT ?", (limit,)
            ).fetchall()
        return [self._repository(row) for row in rows]

    def set_followed(self, repo_id: int, followed: bool, at: str) -> bool:
        with closing(self._connect()) as connection, connection:
            if followed:
                exists = connection.execute(
                    "SELECT 1 FROM repositories WHERE id=?", (repo_id,)
                ).fetchone()
                if not exists:
                    raise ValueError("项目尚未保存在本机")
                connection.execute(
                    "INSERT OR IGNORE INTO follows(repo_id, followed_at) VALUES (?, ?)",
                    (repo_id, at),
                )
            else:
                connection.execute("DELETE FROM follows WHERE repo_id=?", (repo_id,))
        return followed

    def is_followed(self, repo_id: int) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM follows WHERE repo_id=?", (repo_id,)
            ).fetchone() is not None

    def followed_at(self, repo_id: int) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT followed_at FROM follows WHERE repo_id=?", (repo_id,)
            ).fetchone()
        return row["followed_at"] if row else None

    def _folder_call(self, operation, *args, write=False):
        from . import follow_folders
        with closing(self._connect()) as connection, connection:
            if write:connection.execute('BEGIN IMMEDIATE')
            return getattr(follow_folders,operation)(connection,*args)

    def list_follow_folders(self):
        return self._folder_call('list_folders')

    def create_follow_folder(self,name,at):
        return self._folder_call('create_folder',name,at,write=True)

    def rename_follow_folder(self,identity,name):
        return self._folder_call('rename_folder',identity,name,write=True)

    def delete_follow_folder(self,identity):
        return self._folder_call('delete_folder',identity,write=True)

    def reorder_follow_folders(self,ids):
        return self._folder_call('reorder_folders',ids,write=True)

    def move_unfiled_to_folder(self,repo_id,folder_id):
        return self._folder_call('move_unfiled',repo_id,folder_id,write=True)

    def follow_folder_ids(self,repo_id):
        return self._folder_call('folder_ids',repo_id)

    def set_follow_folders(self,repo_id,ids):
        return self._folder_call('set_folders',repo_id,ids,write=True)

    def move_followed_project(self,repo_id,source,target):
        return self._folder_call('move_project',repo_id,source,target,write=True)

    def classify_followed_project(self,repo_id,ids,create_name,at):
        return self._folder_call('classify_project',repo_id,ids,create_name,at,write=True)

    def followed_repositories(self, folder='all') -> list[tuple[Repository, str, StarSnapshot | None]]:
        from .follow_folders import following_filter
        with closing(self._connect()) as connection:
            where,params=following_filter(connection,folder)
            rows = connection.execute(
                """SELECT repo.*, f.followed_at,
                    snap.local_date AS snapshot_date,
                    snap.stars AS snapshot_stars,
                    snap.observed_at AS snapshot_at
                FROM follows AS f JOIN repositories AS repo ON repo.id=f.repo_id
                LEFT JOIN snapshots AS snap ON snap.repo_id=repo.id
                    AND snap.local_date=(SELECT MAX(local_date) FROM snapshots
                                         WHERE repo_id=repo.id)
                """+where+" ORDER BY f.followed_at DESC, repo.id",params
            ).fetchall()
        return [
            (
                self._repository(row), row["followed_at"],
                StarSnapshot(row["id"], row["snapshot_date"],
                             row["snapshot_stars"], row["snapshot_at"])
                if row["snapshot_date"] is not None else None,
            )
            for row in rows
        ]

    def recent_growth_repositories(self, before_date: str,
                                   limit: int = 5) -> list[Repository]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT repo.* FROM recommendations AS rec
                JOIN repositories AS repo ON repo.id=rec.repo_id
                WHERE rec.section='growth' AND rec.local_date=(
                    SELECT MAX(local_date) FROM recommendations
                    WHERE section='growth' AND local_date < ?)
                ORDER BY rec.position LIMIT ?""",
                (before_date, max(0, limit)),
            ).fetchall()
        return [self._repository(row) for row in rows]

    def repositories_for_ids(self, repo_ids: list[int]) -> dict[int, Repository]:
        if not repo_ids:
            return {}
        placeholders = ",".join("?" for _ in repo_ids)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM repositories WHERE id IN ({placeholders})", repo_ids
            ).fetchall()
        return {row["id"]: self._repository(row) for row in rows}

    def snapshots_before(self, local_date: str, repo_ids: list[int]) -> dict[int, StarSnapshot]:
        if not repo_ids:
            return {}
        placeholders = ",".join("?" for _ in repo_ids)
        sql = (
            "SELECT repo_id, local_date, stars, observed_at FROM snapshots "
            f"WHERE local_date < ? AND repo_id IN ({placeholders}) "
            "ORDER BY local_date DESC"
        )
        with closing(self._connect()) as connection:
            rows = connection.execute(sql, (local_date, *repo_ids)).fetchall()
        result = {}
        for row in rows:
            result.setdefault(
                row["repo_id"],
                StarSnapshot(row["repo_id"], row["local_date"], row["stars"], row["observed_at"]),
            )
        return result

    def snapshots_on(self, local_date: str, repo_ids: list[int]) -> dict[int, StarSnapshot]:
        if not repo_ids:
            return {}
        placeholders = ",".join("?" for _ in repo_ids)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT repo_id, local_date, stars, observed_at FROM snapshots "
                f"WHERE local_date=? AND repo_id IN ({placeholders})",
                (local_date, *repo_ids),
            ).fetchall()
        return {
            row["repo_id"]: StarSnapshot(
                row["repo_id"], row["local_date"], row["stars"], row["observed_at"]
            )
            for row in rows
        }

    def seen_repo_ids(self) -> set[int]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT repo_id FROM recommendations UNION SELECT repo_id FROM ai_removed_recommendations UNION SELECT repo_id FROM displayed_repositories"
            ).fetchall()
        return {row["repo_id"] for row in rows}

    @staticmethod
    def _model_key(model_id: str | None) -> str:
        return model_id if model_id is not None else "auto"

    def save_ai_model(self, model_id: str | None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO settings(name, value) VALUES ('ai_model', ?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (self._model_key(model_id),),
            )

    def load_ai_model(self) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE name='ai_model'").fetchone()
        return None if row is None or row["value"] == "auto" else row["value"]

    def load_preferences(self) -> tuple[str, str]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT name, value FROM settings WHERE name IN ('language', 'font_size')"
            ).fetchall()
        saved = {row["name"]: row["value"] for row in rows}
        language = saved.get("language", "zh")
        font_size = saved.get("font_size", "normal")
        return (language if language in {"zh", "en"} else "zh",
                font_size if font_size in {"small", "normal", "large"} else "normal")

    def load_motion_preference(self) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM settings WHERE name='motion_preference'").fetchone()
        return row[0] if row and row[0] in ('system', 'on', 'off') else 'system'

    def save_motion_preference(self, value: str) -> None:
        if value not in ('system', 'on', 'off'): raise ValueError('动效选项无效')
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO settings VALUES ('motion_preference',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (value,))

    def load_display_preferences(self) -> tuple[str, float]:
        language, legacy_size = self.load_preferences()
        fallback = {"small": .9375, "normal": 1.0, "large": 1.125}[legacy_size]
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM settings WHERE name='font_scale'").fetchone()
        try:
            scale = float(row["value"]) if row is not None else fallback
        except (TypeError, ValueError):
            scale = fallback
        if not math.isfinite(scale) or not .8 <= scale <= 2:
            scale = fallback
        return language, scale

    def save_display_preferences(self, language: str, font_scale: float) -> None:
        if (language not in ("zh", "en") or isinstance(font_scale, bool)
                or not isinstance(font_scale, (int, float))
                or not .8 <= font_scale <= 2 or not math.isfinite(font_scale)):
            raise ValueError("语言或字号倍率无效")
        with closing(self._connect()) as connection, connection:
            connection.executemany("""INSERT INTO settings(name,value) VALUES (?,?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (("language", language), ("font_scale", str(float(font_scale)))))

    def load_auto_update_settings(self) -> AutoUpdateSettings:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT name, value FROM settings WHERE name IN ('auto_enabled', 'auto_time')"
            ).fetchall()
        saved = {row["name"]: row["value"] for row in rows}
        time = saved.get("auto_time", "09:00")
        return AutoUpdateSettings(saved.get("auto_enabled", "1") == "1",
                                  time if valid_update_time(time) else "09:00")

    def save_auto_update_settings(self, enabled: bool, time: str) -> None:
        if type(enabled) is not bool or not valid_update_time(time):
            raise ValueError("每日更新时间或开关无效")
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """INSERT INTO settings(name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (("auto_enabled", str(int(enabled))), ("auto_time", time)),
            )

    def load_scheduler_binding(self) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE name='scheduler_binding'").fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return {}

    def save_scheduler_binding(self, binding: dict | None) -> None:
        with closing(self._connect()) as connection, connection:
            if binding is None:
                connection.execute("DELETE FROM settings WHERE name='scheduler_binding'")
            else:
                connection.execute(
                    """INSERT INTO settings(name, value) VALUES ('scheduler_binding', ?)
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                    (json.dumps(binding, ensure_ascii=False),),
                )

    def auto_attempts(self, local_date: str) -> AutoAttemptState:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM auto_update_runs WHERE local_date=?", (local_date,)
            ).fetchone()
        return (AutoAttemptState(row["attempts"], row["last_attempt_at"],
                                 row["status"], row["reason"])
                if row else AutoAttemptState())

    def latest_auto_attempt(self) -> AutoAttemptState:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM auto_update_runs ORDER BY local_date DESC LIMIT 1"
            ).fetchone()
        return (AutoAttemptState(row["attempts"], row["last_attempt_at"],
                                 row["status"], row["reason"])
                if row else AutoAttemptState())

    def begin_auto_attempt(self, local_date: str, observed_at: str) -> bool:
        from datetime import datetime, timedelta

        now = datetime.fromisoformat(observed_at)
        if now.tzinfo is None or now.date().isoformat() != local_date:
            raise ValueError("自动更新日期无效")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM daily_runs WHERE local_date=?", (local_date,)
            ).fetchone():
                return False
            row = connection.execute(
                "SELECT attempts, last_attempt_at FROM auto_update_runs WHERE local_date=?",
                (local_date,),
            ).fetchone()
            if row and (row["attempts"] >= 3 or
                        (row["last_attempt_at"] and now - datetime.fromisoformat(
                            row["last_attempt_at"]) < timedelta(hours=1))):
                return False
            connection.execute(
                """INSERT INTO auto_update_runs
                (local_date, attempts, last_attempt_at, status, reason)
                VALUES (?, 1, ?, 'running', NULL)
                ON CONFLICT(local_date) DO UPDATE SET
                attempts=auto_update_runs.attempts+1,
                last_attempt_at=excluded.last_attempt_at,
                status='running', reason=NULL""",
                (local_date, observed_at),
            )
        return True

    def finish_auto_attempt(self, local_date: str, status: str,
                            reason: str | None = None) -> None:
        if status not in {"success", "error", "busy"}:
            raise ValueError("自动更新结果无效")
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                """UPDATE auto_update_runs SET status=?, reason=?
                WHERE local_date=? AND status='running'""",
                (status, reason, local_date),
            )
            if updated.rowcount != 1:
                raise ValueError("没有正在进行的自动更新")

    def save_preferences(self, language: str, font_size: str) -> None:
        if language not in {"zh", "en"} or font_size not in {"small", "normal", "large"}:
            raise ValueError("语言或字号无效")
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """INSERT INTO settings(name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (("language", language), ("font_size", font_size),
                 ("font_scale", str({"small": .9375, "normal": 1.0, "large": 1.125}[font_size]))),
            )

    def save_ai_active_model(self, model_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO settings(name, value) VALUES ('ai_active_model', ?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                (model_id,),
            )

    def load_ai_active_model(self) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE name='ai_active_model'").fetchone()
        return row["value"] if row else None

    def ai_progress(self, local_date: str, keyword_id: int,
                    model_id: str | None) -> AIProgress:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT next_page, next_offset, checked_count FROM ai_keyword_progress
                WHERE local_date=? AND keyword_id=? AND model_key=?""",
                (local_date, keyword_id, self._model_key(model_id)),
            ).fetchone()
        return AIProgress(row["next_page"], row["next_offset"], row["checked_count"]) \
            if row else AIProgress(1, 0, 0)

    def ai_verdicts(self, local_date: str, keyword_id: int,
                    model_id: str | None) -> dict[int, RelevanceVerdict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT repo_id, verdict, reason FROM ai_verdicts
                WHERE local_date=? AND keyword_id=? AND model_key=?""",
                (local_date, keyword_id, self._model_key(model_id)),
            ).fetchall()
        return {row["repo_id"]: RelevanceVerdict(row["repo_id"], row["verdict"],
                                                   row["reason"]) for row in rows}

    def commit_ai_batch(self, local_date: str, keyword_id: int, model_id: str | None,
                        batch: CandidateBatch, verdicts: tuple[RelevanceVerdict, ...],
                        checked_at: str) -> None:
        ids = [item.repo.id for item in batch.inputs]
        decisions = {item.repo_id: item for item in verdicts}
        if (not ids or len(ids) > 20 or len(ids) != len(set(ids))
                or len(decisions) != len(ids) or set(decisions) != set(ids)
                or batch.next_page < 1 or batch.next_offset < 0):
            raise ValueError("AI 批次仓库或游标无效")
        if any(item.verdict not in {"relevant", "irrelevant", "uncertain"}
               or not item.reason.strip() for item in verdicts):
            raise ValueError("AI 判断无效")
        model_key = self._model_key(model_id)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            rule = connection.execute(
                "SELECT enabled FROM keywords WHERE id=?", (keyword_id,)).fetchone()
            if rule is None or not rule["enabled"]:
                raise ValueError("关键词不存在或已停用")
            for item in batch.inputs:
                repo = item.repo
                connection.execute(
                    """INSERT INTO repositories
                    (id, full_name, html_url, description, topics, language, stars, archived)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET full_name=excluded.full_name,
                    html_url=excluded.html_url, description=excluded.description,
                    topics=excluded.topics, language=excluded.language,
                    stars=excluded.stars, archived=excluded.archived""",
                    (repo.id, repo.full_name, repo.html_url, repo.description,
                     json.dumps(repo.topics), repo.language, repo.stars, int(repo.archived)),
                )
                # Cached metadata carries no new observation timestamp. In particular,
                # judging an old card cannot manufacture today's Star snapshot.
                if item.observed_at is not None:
                    if datetime.fromisoformat(item.observed_at).date().isoformat() != local_date:
                        raise ValueError("仓库观察日期与快照日期不一致")
                    connection.execute("""INSERT INTO snapshots VALUES(?,?,?,?)
                        ON CONFLICT(repo_id,local_date) DO UPDATE SET
                        stars=excluded.stars,observed_at=excluded.observed_at
                        WHERE excluded.observed_at>=snapshots.observed_at""",
                        (repo.id,local_date,repo.stars,item.observed_at))
                verdict = decisions[repo.id]
                connection.execute(
                    """INSERT INTO ai_verdicts
                    (local_date, keyword_id, model_key, repo_id, verdict, reason,
                     source_limited, checked_at, candidate_rank) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (local_date, keyword_id, model_key, repo.id, verdict.verdict,
                     verdict.reason, int(item.source_limited), checked_at, item.candidate_rank),
                )
            existing = connection.execute(
                """SELECT repo_id FROM recommendations WHERE local_date=?
                AND section='keyword' AND keyword_id=?""",
                (local_date, keyword_id),
            ).fetchall()
            judged = {
                row["repo_id"]: row["verdict"] for row in connection.execute(
                    """SELECT repo_id, verdict FROM ai_verdicts
                    WHERE local_date=? AND keyword_id=? AND model_key=?""",
                    (local_date, keyword_id, model_key),
                ).fetchall()
            }
            for row in existing:
                repo_id = row["repo_id"]
                if judged.get(repo_id) not in {"irrelevant", "uncertain"}:
                    continue
                connection.execute(
                    """DELETE FROM recommendations WHERE local_date=? AND section='keyword'
                    AND keyword_id=? AND repo_id=?""",
                    (local_date, keyword_id, repo_id),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO ai_removed_recommendations
                    (local_date, keyword_id, repo_id, removed_at, verdict)
                    VALUES (?, ?, ?, ?, ?)""",
                    (local_date, keyword_id, repo_id, checked_at, judged[repo_id]),
                )
            # Compare the complete eligible frontier rather than just filling holes.
            # A lower-Star incumbent is allowed to leave, while its visibility is
            # retained by the delete trigger for cross-day de-duplication.
            candidates = connection.execute("""SELECT repo.id,
                COALESCE(v.candidate_rank,rec.rank) AS candidate_rank
                FROM repositories repo
                LEFT JOIN ai_verdicts v ON v.repo_id=repo.id AND v.local_date=?
                    AND v.keyword_id=? AND v.model_key=?
                LEFT JOIN recommendations rec ON rec.repo_id=repo.id AND rec.local_date=?
                    AND rec.section='keyword' AND rec.keyword_id=?
                WHERE (v.verdict='relevant' OR (rec.repo_id IS NOT NULL AND v.verdict IS NULL))
                    AND repo.archived=0
                    AND repo.stars >= (SELECT min_stars FROM keywords WHERE id=?)
                    AND NOT EXISTS (SELECT 1 FROM displayed_repositories d
                                    WHERE d.repo_id=repo.id AND d.local_date<?)
                    AND NOT EXISTS (SELECT 1 FROM recommendations other
                        WHERE other.repo_id=repo.id AND other.local_date=?
                          AND NOT(other.section='keyword' AND other.keyword_id=?))
                ORDER BY repo.stars DESC,repo.id LIMIT 5""",
                (local_date,keyword_id,model_key,local_date,keyword_id,keyword_id,
                 local_date,local_date,keyword_id)).fetchall()
            chosen = {r['id'] for r in candidates}
            for row in existing:
                if row['repo_id'] not in chosen:
                    connection.execute("DELETE FROM recommendations WHERE local_date=? AND repo_id=? AND section='keyword' AND keyword_id=?",
                                       (local_date,row['repo_id'],keyword_id))
            for row in candidates:
                connection.execute("""INSERT OR IGNORE INTO recommendations
                    (repo_id,local_date,section,keyword_id,star_delta,baseline_at,
                     observed_at,position,metric_basis,metric_date,rank)
                    VALUES(?,?,'keyword',?,NULL,NULL,?,0,NULL,NULL,?)""",
                    (row['id'],local_date,keyword_id,checked_at,row['candidate_rank']))
            ordered = connection.execute(
                """SELECT rec.repo_id, rec.section, rec.keyword_id FROM recommendations AS rec
                JOIN repositories AS repo ON repo.id=rec.repo_id
                WHERE rec.local_date=?
                ORDER BY CASE rec.section WHEN 'growth' THEN 0 ELSE 1 END,
                    COALESCE(rec.keyword_id, 0),
                    CASE rec.section WHEN 'growth' THEN rec.position ELSE 0 END,
                    CASE rec.section WHEN 'growth' THEN 0 ELSE repo.stars END DESC,
                    rec.repo_id""",
                (local_date,),
            ).fetchall()
            for position, row in enumerate(ordered, 1):
                connection.execute(
                    "UPDATE recommendations SET position=? WHERE local_date=? AND repo_id=?",
                    (position, local_date, row["repo_id"]),
                )
            progress = connection.execute(
                """SELECT checked_count FROM ai_keyword_progress
                WHERE local_date=? AND keyword_id=? AND model_key=?""",
                (local_date, keyword_id, model_key),
            ).fetchone()
            checked_count = (progress["checked_count"] if progress else 0) + len(ids)
            connection.execute(
                """INSERT INTO ai_keyword_progress
                (local_date, keyword_id, model_key, next_page, next_offset, checked_count)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_date, keyword_id, model_key) DO UPDATE SET
                next_page=excluded.next_page, next_offset=excluded.next_offset,
                checked_count=excluded.checked_count""",
                (local_date, keyword_id, model_key, batch.next_page,
                 batch.next_offset, checked_count),
            )

    def save_explanation(self, repo_id: int, keyword_id: int | None,
                         model_id: str | None, source_hash: str,
                         explanation: ProjectExplanation) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if keyword_id is not None:
                rule = connection.execute(
                    "SELECT enabled, deleted_at FROM keywords WHERE id=?", (keyword_id,)
                ).fetchone()
                if rule is not None and (not rule["enabled"] or rule["deleted_at"] is not None):
                    raise ValueError("关键词不存在或已停用")
            connection.execute(
                """INSERT INTO ai_explanations
                (repo_id, keyword_key, model_key, source_hash, content)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, keyword_key, model_key, source_hash) DO UPDATE SET
                content=excluded.content, saved_at=CURRENT_TIMESTAMP""",
                (repo_id, keyword_id or 0, self._model_key(model_id), source_hash,
                 json.dumps(asdict(explanation), ensure_ascii=False)),
            )

    def load_explanation(self, repo_id: int, keyword_id: int | None,
                         model_id: str | None, source_hash: str) -> ProjectExplanation | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT content FROM ai_explanations WHERE repo_id=?
                AND keyword_key=? AND model_key=? AND source_hash=?""",
                (repo_id, keyword_id or 0, self._model_key(model_id), source_hash),
            ).fetchone()
        if row is None:
            return None
        return self._explanation(row["content"])

    def latest_explanation(self, repo_id: int, keyword_id: int | None,
                           model_id: str | None) -> tuple[ProjectExplanation, str] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT content, saved_at FROM ai_explanations WHERE repo_id=?
                AND keyword_key=? AND model_key=? ORDER BY saved_at DESC, rowid DESC LIMIT 1""",
                (repo_id, keyword_id or 0, self._model_key(model_id)),
            ).fetchone()
        return (self._explanation(row["content"]), row["saved_at"]) if row else None

    def latest_explanation_any_model(self, repo_id: int, keyword_id: int | None
                                     ) -> tuple[ProjectExplanation, str, str | None] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT content, saved_at, model_key FROM ai_explanations WHERE repo_id=?
                AND keyword_key=? ORDER BY saved_at DESC, rowid DESC LIMIT 1""",
                (repo_id, keyword_id or 0),
            ).fetchone()
        if row is None:
            return None
        model_id = None if row["model_key"] == "auto" else row["model_key"]
        return self._explanation(row["content"]), row["saved_at"], model_id

    def latest_explanation_for_repo(self, repo_id: int, preferred_model: str | None
                                    ) -> tuple[ProjectExplanation, str, str | None] | None:
        """Read a followed repository's saved AI text after its recommendation was removed."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT content, saved_at, model_key FROM ai_explanations
                WHERE repo_id=? ORDER BY CASE WHEN model_key=? THEN 0 ELSE 1 END,
                saved_at DESC, rowid DESC LIMIT 1""",
                (repo_id, self._model_key(preferred_model)),
            ).fetchone()
        if row is None:
            return None
        model_id = None if row["model_key"] == "auto" else row["model_key"]
        return self._explanation(row["content"]), row["saved_at"], model_id

    @staticmethod
    def _explanation(content: str) -> ProjectExplanation:
        data = json.loads(content)
        from .ai_types import ProjectKind
        kind=data.get("project_kind")
        if kind is not None:kind=ProjectKind(**{**kind,"secondary":tuple(kind["secondary"]),"evidence":tuple(kind["evidence"])})
        return ProjectExplanation(
            InsightText(**{**data["zh"], "highlights": tuple(data["zh"]["highlights"])}),
            InsightText(**{**data["en"], "highlights": tuple(data["en"]["highlights"])}),
            data["relevance"], tuple(data["evidence"]), data["source_limited"],
            data.get('schema_version',1),data.get('readme_hash'),kind,
        )

    def latest_snapshot_at(self) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT observed_at FROM snapshots "
                "ORDER BY local_date DESC, observed_at DESC LIMIT 1"
            ).fetchone()
        return row["observed_at"] if row else None

    def latest_successful_date(self) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT MAX(local_date) AS date FROM daily_runs").fetchone()
        return row["date"]

    def daily_updated_at(self, local_date: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT completed_at FROM daily_runs WHERE local_date=?", (local_date,)
            ).fetchone()
        return row["completed_at"] if row else None

    def latest_refresh_failure(self) -> tuple[str, str] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT attempted_at, reason FROM refresh_failure WHERE id=1"
            ).fetchone()
        return (row["attempted_at"], row["reason"]) if row else None

    def save_refresh_failure(self, attempted_at: str, reason: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO refresh_failure(id, attempted_at, reason) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                attempted_at=excluded.attempted_at, reason=excluded.reason""",
                (attempted_at, reason),
            )

    def daily_sections(self, local_date: str) -> set[tuple[str, int | None]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT section, keyword_id FROM section_runs WHERE local_date=?",
                (local_date,),
            ).fetchall()
        return {
            (row["section"], row["keyword_id"] or None)
            for row in rows
        }

    def daily_recommendations(self, local_date: str) -> list[Recommendation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM recommendations WHERE local_date=? ORDER BY position", (local_date,)
            ).fetchall()
        return [self._recommendation(row) for row in rows]

    def history_dates(self) -> list[tuple[str, int]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT local_date, COUNT(*) AS item_count FROM recommendations "
                "GROUP BY local_date ORDER BY local_date DESC"
            ).fetchall()
        return [(row["local_date"], row["item_count"]) for row in rows]

    def search_history(self, filters) -> list[Recommendation]:
        from .history_search import search_rows
        with closing(self._connect()) as connection:
            rows=search_rows(connection,filters)
        return [self._recommendation(row) for row in rows]

    def history_month(self, month: str) -> list[tuple[str, int]]:
        from .history_search import month_bounds
        start,end=month_bounds(month)
        with closing(self._connect()) as connection:
            rows=connection.execute('SELECT local_date,COUNT(*) AS item_count FROM recommendations '
                                    'WHERE local_date>=? AND local_date<=? GROUP BY local_date ORDER BY local_date',
                                    (start,end)).fetchall()
        return [(row['local_date'],row['item_count']) for row in rows]

    def recommendation_on(self, local_date: str, repo_id: int) -> Recommendation | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM recommendations WHERE local_date=? AND repo_id=?",
                (local_date, repo_id),
            ).fetchone()
        return self._recommendation(row) if row else None

    def latest_recommendation_for_repo(self, repo_id: int) -> Recommendation | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM recommendations WHERE repo_id=? "
                "ORDER BY local_date DESC LIMIT 1", (repo_id,)
            ).fetchone()
        return self._recommendation(row) if row else None

    def growth_coverage(self, local_date: str) -> GrowthCoverage | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM growth_runs WHERE local_date=?", (local_date,)
            ).fetchone()
        if row is None:
            return None
        return GrowthCoverage(
            row["candidate_count"], row["scored_count"],
            tuple(json.loads(row["source_names"])), row["stat_date"], row["metric_basis"],
            row["catalog_count"], row["failed_count"], tuple(json.loads(row["stop_reasons"])),
        )

    def catalog_count(self) -> int:
        with closing(self._connect()) as connection:
            return connection.execute("SELECT COUNT(*) FROM discovery_catalog").fetchone()[0]

    def official_star_evidence(self, repo_id: int, observed_at: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM official_star_evidence WHERE repo_id=? AND observed_at=? "
                                      "ORDER BY week,day_index", (repo_id, observed_at)).fetchall()
        return [dict(row) for row in rows]

    def mark_catalog_scored(self, repo_id: int, observed_at: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE discovery_catalog SET last_scored_at=? WHERE repo_id=?",
                               (observed_at, repo_id))

    @staticmethod
    def _write_cross_checks(connection, local_date, checks):
        for item in checks:
            connection.execute("INSERT OR REPLACE INTO source_cross_checks VALUES (?,?,?,?,?)",
                               (local_date,item.repo_id,item.metric,item.source_url or "",json.dumps(asdict(item))))

    def save_cross_checks(self, local_date: str, checks: list[CrossCheck]) -> None:
        with closing(self._connect()) as connection, connection:
            self._write_cross_checks(connection, local_date, checks)

    def cross_checks(self, local_date: str, repo_id: int) -> list[CrossCheck]:
        with closing(self._connect()) as connection:
            rows=connection.execute("SELECT payload FROM source_cross_checks WHERE local_date=? AND repo_id=? "
                                    "ORDER BY source_key, CASE metric WHEN 'total_stars' THEN 0 ELSE 1 END",
                                    (local_date,repo_id)).fetchall()
        return [CrossCheck(**json.loads(row[0])) for row in rows]

    def latest_recommendations(self) -> list[Recommendation]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM recommendations WHERE local_date="
                "(SELECT MAX(local_date) FROM recommendations) ORDER BY position"
            ).fetchall()
        return [self._recommendation(row) for row in rows]

    def commit_daily(
        self,
        local_date: str,
        repos: list[Repository],
        snapshots: list[StarSnapshot],
        recommendations: list[Recommendation],
        *,
        completed_at: str | None = None,
        completed_sections: list[tuple[str, int | None]] | None = None,
        growth_coverage: GrowthCoverage | None = None,
        cross_checks: list[CrossCheck] | None = None,
        official_weeks: dict | None = None,
        _connection: sqlite3.Connection | None = None,
    ) -> None:
        repo_ids = [item.repo_id for item in recommendations]
        if len(repo_ids) != len(set(repo_ids)):
            raise ValueError("同一仓库不能重复推荐")
        if any(item.local_date != local_date for item in (*snapshots, *recommendations)):
            raise ValueError("记录日期与更新日期不一致")
        if completed_at is None:
            completed_at = max(
                (item.observed_at for item in (*snapshots, *recommendations)), default=None
            )
        if completed_sections is None:
            completed_sections = list({(item.section, item.keyword_id) for item in recommendations})
        with (closing(self._connect()) if _connection is None else nullcontext(_connection)) as connection, \
                (connection if _connection is None else nullcontext()):
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            blocked_ids = {
                row["id"] for row in connection.execute(
                    "SELECT id FROM keywords WHERE enabled=0 OR deleted_at IS NOT NULL"
                )
            }
            recommendations = [
                item for item in recommendations
                if item.section != "keyword" or item.keyword_id not in blocked_ids
            ]
            completed_sections = [
                (section, keyword_id) for section, keyword_id in completed_sections
                if section != "keyword" or keyword_id not in blocked_ids
            ]
            next_position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM recommendations WHERE local_date=?",
                (local_date,),
            ).fetchone()[0]
            for repo in repos:
                connection.execute(
                    """INSERT INTO repositories
                    (id, full_name, html_url, description, topics, language, stars, archived)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                    full_name=excluded.full_name, html_url=excluded.html_url,
                    description=excluded.description, topics=excluded.topics,
                    language=excluded.language, stars=excluded.stars, archived=excluded.archived""",
                    (
                        repo.id, repo.full_name, repo.html_url, repo.description,
                        json.dumps(repo.topics), repo.language, repo.stars, int(repo.archived),
                    ),
                )
            for item in snapshots:
                connection.execute(
                    """INSERT INTO snapshots(repo_id, local_date, stars, observed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(repo_id, local_date) DO UPDATE SET
                    stars=excluded.stars, observed_at=excluded.observed_at""",
                    (item.repo_id, item.local_date, item.stars, item.observed_at),
                )
            for repo_id, weeks in (official_weeks or {}).items():
                for week in weeks:
                    for day_index, added in enumerate(week.days):
                        connection.execute("INSERT OR REPLACE INTO official_star_evidence VALUES (?,?,?,?,?,?)",
                            (repo_id, completed_at, week.week, day_index, added,
                             "v2: UTC Sunday week + index; end threshold label + 1 day; unfinished UTC day excluded"))
            for item in recommendations:
                try:
                    connection.execute(
                        """INSERT INTO recommendations
                        (repo_id, local_date, section, keyword_id, star_delta, baseline_at,
                         observed_at, position, metric_basis, metric_date,rank,display_role,matched_keyword_ids)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            item.repo_id, item.local_date, item.section, item.keyword_id,
                            item.star_delta, item.baseline_at, item.observed_at, next_position,
                            item.metric_basis, item.metric_date, item.rank, item.display_role,
                            json.dumps(item.matched_keyword_ids),
                        ),
                    )
                    next_position += 1
                except sqlite3.IntegrityError as exc:
                    raise ValueError("同一仓库不能重复推荐") from exc
            if completed_at is not None:
                connection.execute(
                    """INSERT INTO daily_runs(local_date, completed_at) VALUES (?, ?)
                    ON CONFLICT(local_date) DO UPDATE SET completed_at=excluded.completed_at""",
                    (local_date, completed_at),
                )
                for section, keyword_id in completed_sections:
                    connection.execute(
                        """INSERT OR IGNORE INTO section_runs(local_date, section, keyword_id)
                        VALUES (?, ?, ?)""",
                        (local_date, section, keyword_id or 0),
                    )
            if growth_coverage is not None:
                connection.execute(
                    """INSERT INTO growth_runs
                    (local_date, candidate_count, scored_count, source_names, stat_date, metric_basis,
                     catalog_count, failed_count, stop_reasons)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(local_date) DO UPDATE SET
                    candidate_count=excluded.candidate_count,
                    scored_count=excluded.scored_count,
                    source_names=excluded.source_names,
                    stat_date=excluded.stat_date,
                    metric_basis=excluded.metric_basis, catalog_count=excluded.catalog_count,
                    failed_count=excluded.failed_count, stop_reasons=excluded.stop_reasons""",
                    (local_date, growth_coverage.candidate_count, growth_coverage.scored_count,
                     json.dumps(growth_coverage.source_names), growth_coverage.stat_date,
                    growth_coverage.metric_basis, growth_coverage.catalog_count, growth_coverage.failed_count,
                    json.dumps(growth_coverage.stop_reasons)),
                )
            if cross_checks is not None:
                self._write_cross_checks(connection, local_date, cross_checks)
            connection.execute("DELETE FROM refresh_failure WHERE id=1")

    def commit_growth_date_update(self, local_date: str, repos: list[Repository],
                                  snapshots: list[StarSnapshot], recommendations: list[Recommendation],
                                  *, completed_at: str, growth_coverage: GrowthCoverage,
                                  expected_stat_date: str, official_weeks: dict,
                                  cross_checks: list[CrossCheck] | None = None,
                                  completed_sections: list[tuple[str,int | None]] | None = None) -> None:
        """Correct only the active issue atomically, archiving its previous data."""
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT * FROM growth_runs WHERE local_date=?',(local_date,)).fetchone()
            latest = connection.execute('SELECT MAX(local_date) FROM daily_runs').fetchone()[0]
            observed = datetime.fromisoformat(completed_at)
            target = (observed.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat()
            if (observed.tzinfo is None or local_date != observed.date().isoformat() or latest != local_date
                    or old is None or old['stat_date'] != expected_stat_date
                    or growth_coverage.stat_date != target or target <= expected_stat_date):
                raise ValueError('仅可纠正当前期未被其他更新替换的旧统计日')
            growth = [r for r in recommendations if r.section=='growth']
            if (sum(r.display_role == 'new' for r in growth) != 5
                    or any(r.metric_date != target or r.metric_basis != 'github_daily_new' for r in growth)):
                raise ValueError('统计日纠正必须使用昨天完整日的官方增长')
            prefix = 'stat-date:'+expected_stat_date+':'
            self._archive_growth(connection,dict(old,repo_id=0),prefix+'coverage')
            original = connection.execute("SELECT * FROM recommendations WHERE local_date=? AND section='growth'",
                                          (local_date,)).fetchall()
            for row in original:
                self._archive_growth(connection,row,prefix+'recommendation')
                snapshot = connection.execute('SELECT * FROM snapshots WHERE local_date=? AND repo_id=?',
                                              (local_date,row['repo_id'])).fetchone()
                if snapshot is not None: self._archive_growth(connection,snapshot,prefix+'snapshot')
            connection.execute("DELETE FROM recommendations WHERE local_date=? AND section='growth'",(local_date,))
            new_ids = {r.repo_id for r in recommendations}
            self.commit_daily(local_date,repos,[s for s in snapshots if s.repo_id in new_ids],recommendations,completed_at=completed_at,
                completed_sections=completed_sections or [('growth',None)],growth_coverage=growth_coverage,
                official_weeks=official_weeks,cross_checks=cross_checks,_connection=connection)
            connection.execute('DELETE FROM legacy_growth_repairs WHERE local_date=?',(local_date,))

    def commit_growth_repair(self, local_date: str, repos: list[Repository],
                             snapshots: list[StarSnapshot], recommendations: list[Recommendation],
                             *, completed_at: str, growth_coverage: GrowthCoverage,
                             official_weeks: dict, cross_checks: list[CrossCheck] | None = None,
                             completed_sections: list[tuple[str,int | None]] | None = None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM legacy_growth_repairs WHERE local_date=?',
                                  (local_date,)).fetchone() is None:
                raise ValueError('该期增长已修复，不重复替换')
            original = connection.execute("SELECT * FROM recommendations WHERE local_date=? "
                                          "AND section='growth'",(local_date,)).fetchall()
            dates = {r['metric_date'] for r in original}
            if dates != {growth_coverage.stat_date} or None in dates:
                raise ValueError('修复增长与原统计日期不一致')
            reserved = self._growth_reserved_ids(connection,local_date)
            growth = [r for r in recommendations if r.section == 'growth']
            validate_growth_repair(growth,local_date,growth_coverage.stat_date,reserved)
            for row in original:
                self._archive_growth(connection,row,'recommendation')
                snapshot = connection.execute('SELECT * FROM snapshots WHERE local_date=? AND repo_id=?',
                                              (local_date,row['repo_id'])).fetchone()
                if snapshot is not None:
                    self._archive_growth(connection,snapshot,'snapshot')
            connection.execute("DELETE FROM recommendations WHERE local_date=? AND section='growth'",
                               (local_date,))
            new_ids = {r.repo_id for r in recommendations}
            self.commit_daily(local_date,repos,[s for s in snapshots if s.repo_id in new_ids],
                recommendations,completed_at=completed_at,
                completed_sections=completed_sections or [('growth',None)],
                growth_coverage=growth_coverage,official_weeks=official_weeks,
                cross_checks=cross_checks,_connection=connection)
            connection.execute('DELETE FROM legacy_growth_repairs WHERE local_date=?',(local_date,))

    @staticmethod
    def _keyword(row: sqlite3.Row) -> KeywordRule:
        return KeywordRule(row["id"], row["term"], row["min_stars"], bool(row["enabled"]))

    @staticmethod
    def _repository(row: sqlite3.Row) -> Repository:
        return Repository(
            row["id"], row["full_name"], row["html_url"], row["description"],
            tuple(json.loads(row["topics"])), row["language"], row["stars"], bool(row["archived"]),
        )

    @staticmethod
    def _recommendation(row: sqlite3.Row) -> Recommendation:
        return Recommendation(
            row["repo_id"], row["local_date"], row["section"], row["keyword_id"],
            row["star_delta"], row["baseline_at"], row["observed_at"],
            row["metric_basis"], row["metric_date"],
            row["rank"], row["display_role"], tuple(json.loads(row["matched_keyword_ids"])),
        )
