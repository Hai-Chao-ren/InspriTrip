from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values

from inspitrip.paths import DEFAULT_ENV_PATH, DEMO_DATA_DIR

from .v2_pipeline import load_jsonl


ENV_VALUES = dotenv_values(DEFAULT_ENV_PATH)


def _claim_author_key(row: dict[str, Any]) -> str:
    return str(
        row.get("author_hash")
        or row.get("note_id")
        or row.get("evidence_id")
        or row.get("claim_id")
        or ""
    )


def _date_sort_value(value: Any) -> int:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    try:
        return int(digits[:8])
    except ValueError:
        return 0


class RecommendationRepository(Protocol):
    def get_profiles(self, destination_ids: list[str]) -> list[dict[str, Any]]: ...

    def get_active_profiles(self) -> list[dict[str, Any]]: ...

    def get_claims(
        self,
        destination_ids: list[str],
        aspects: list[str],
        *,
        per_destination: int = 4,
        polarities: tuple[str, ...] = (),
        entity_types: tuple[str, ...] = (),
        tag_ids: tuple[str, ...] = (),
    ) -> dict[str, list[dict[str, Any]]]: ...

    def get_travel_rows(self, destination_ids: list[str]) -> list[dict[str, Any]]: ...


class JsonlRecommendationRepository:
    """Local/offline repository using the generated v2 JSONL snapshot."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._profiles = load_jsonl(data_dir / "destination_profiles.jsonl")
        self._claims = load_jsonl(data_dir / "ugc_claims.jsonl")
        self._entity_by_id = {
            str(row.get("entity_id") or ""): row
            for row in load_jsonl(data_dir / "entities.jsonl")
            if row.get("entity_id")
        }
        self._entity_type_by_id = {
            str(row.get("entity_id") or ""): str(row.get("entity_type") or "")
            for row in self._entity_by_id.values()
        }
        self._travel = load_jsonl(data_dir / "travel_matrix.jsonl")

    def get_profiles(self, destination_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(destination_ids)
        result = []
        for profile in self._profiles:
            destination_id = str(profile.get("destination_id") or "")
            if destination_id not in wanted:
                continue
            entity = self._entity_by_id.get(destination_id) or {}
            result.append({**entity, **profile, "destination_id": destination_id})
        return result

    def get_active_profiles(self) -> list[dict[str, Any]]:
        result = []
        for profile in self._profiles:
            destination_id = str(profile.get("destination_id") or "")
            entity = self._entity_by_id.get(destination_id) or {}
            if profile.get("status") != "active" or entity.get("entity_type") != "destination":
                continue
            result.append({**entity, **profile, "destination_id": destination_id})
        return result

    def get_claims(
        self,
        destination_ids: list[str],
        aspects: list[str],
        *,
        per_destination: int = 4,
        polarities: tuple[str, ...] = (),
        entity_types: tuple[str, ...] = (),
        tag_ids: tuple[str, ...] = (),
    ) -> dict[str, list[dict[str, Any]]]:
        wanted = set(destination_ids)
        aspect_set = set(aspects)
        polarity_set = set(polarities)
        entity_type_set = set(entity_types)
        tag_set = set(tag_ids)
        result: dict[str, list[dict[str, Any]]] = {destination_id: [] for destination_id in destination_ids}
        rows: list[dict[str, Any]] = []
        for source_row in self._claims:
            entity_type = self._entity_type_by_id.get(str(source_row.get("entity_id") or ""), "")
            claim_tags = (
                set(source_row.get("mood") or [])
                | set(source_row.get("vibe") or [])
                | set(source_row.get("activity") or [])
            )
            if source_row.get("destination_id") not in wanted:
                continue
            if source_row.get("is_suspected_ad"):
                continue
            if polarity_set and source_row.get("polarity") not in polarity_set:
                continue
            if entity_type_set and entity_type not in entity_type_set:
                continue
            if (aspect_set or tag_set) and not (
                source_row.get("aspect") in aspect_set or claim_tags & tag_set
            ):
                continue
            row = dict(source_row)
            row["entity_type"] = entity_type
            rows.append(row)
        rows.sort(
            key=lambda row: (
                row.get("destination_id", ""),
                -float(row.get("source_quality") or 0),
                -_date_sort_value(row.get("publish_date")),
                str(row.get("claim_id") or ""),
            )
        )
        seen_authors: dict[str, set[str]] = {
            destination_id: set() for destination_id in destination_ids
        }
        for row in rows:
            bucket = result[row["destination_id"]]
            author_key = _claim_author_key(row)
            if author_key in seen_authors[row["destination_id"]]:
                continue
            if len(bucket) >= per_destination:
                continue
            seen_authors[row["destination_id"]].add(author_key)
            bucket.append(row)
        return result

    def get_travel_rows(self, destination_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(destination_ids)
        return [row for row in self._travel if row.get("destination_id") in wanted]


class PostgresRecommendationRepository:
    def __init__(self, database_url: str):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency.
            raise RuntimeError("使用 PostgreSQL 仓库需安装 psycopg[binary]") from exc
        self._psycopg = psycopg
        self.database_url = database_url

    def get_profiles(self, destination_ids: list[str]) -> list[dict[str, Any]]:
        if not destination_ids:
            return []
        query = """
            SELECT e.entity_id AS destination_id, e.name, e.aliases, e.city,
                   e.province, e.category, e.status, e.longitude, e.latitude,
                   e.map_poi_id, e.standard_province, e.standard_city,
                   e.standard_district, e.adcode, e.address, e.telephone,
                   e.business_area, e.opening_hours, e.map_operational_status,
                   e.map_match_confidence, e.map_match_level, e.geocode_status,
                   e.map_review_status, e.map_checked_at, e.map_source,
                   p.mood_scores, p.vibe_scores, p.activity_scores,
                   p.core_feeling, p.atmosphere, p.suitable_scenes,
                   p.activities, p.limitations, p.positive_evidence_count,
                   p.limitation_evidence_count,
                   p.evidence_quality, p.freshness_score,
                   p.private_discovery_value, p.source_count,
                   to_jsonb(f) - 'destination_id' AS metadata
              FROM recommendation_entities e
              JOIN destination_profiles p ON p.destination_id = e.entity_id
         LEFT JOIN destination_facts f ON f.destination_id = e.entity_id
             WHERE e.entity_id = ANY(%s)
        """
        with self._psycopg.connect(self.database_url) as connection:
            with connection.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute(query, (destination_ids,))
                return [dict(row) for row in cursor.fetchall()]

    def get_active_profiles(self) -> list[dict[str, Any]]:
        query = """
            SELECT e.entity_id AS destination_id, e.name, e.aliases, e.city,
                   e.province, e.category, e.status, e.longitude, e.latitude,
                   e.map_poi_id, e.standard_province, e.standard_city,
                   e.standard_district, e.adcode, e.address, e.telephone,
                   e.business_area, e.opening_hours, e.map_operational_status,
                   e.map_match_confidence, e.map_match_level, e.geocode_status,
                   e.map_review_status, e.map_checked_at, e.map_source,
                   p.mood_scores, p.vibe_scores, p.activity_scores,
                   p.core_feeling, p.atmosphere, p.suitable_scenes,
                   p.activities, p.limitations, p.positive_evidence_count,
                   p.limitation_evidence_count,
                   p.evidence_quality, p.freshness_score,
                   p.private_discovery_value, p.source_count,
                   to_jsonb(f) - 'destination_id' AS metadata
              FROM recommendation_entities e
              JOIN destination_profiles p ON p.destination_id = e.entity_id
         LEFT JOIN destination_facts f ON f.destination_id = e.entity_id
             WHERE e.entity_type = 'destination'
               AND e.status = 'active'
          ORDER BY e.entity_id
        """
        with self._psycopg.connect(self.database_url) as connection:
            with connection.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]

    def get_claims(
        self,
        destination_ids: list[str],
        aspects: list[str],
        *,
        per_destination: int = 4,
        polarities: tuple[str, ...] = (),
        entity_types: tuple[str, ...] = (),
        tag_ids: tuple[str, ...] = (),
    ) -> dict[str, list[dict[str, Any]]]:
        result = {destination_id: [] for destination_id in destination_ids}
        if not destination_ids:
            return result
        query = """
            SELECT c.*, e.entity_type
              FROM ugc_evidence_claims c
              JOIN recommendation_entities e ON e.entity_id = c.entity_id
             WHERE c.destination_id = ANY(%s)
               AND c.is_suspected_ad = false
               AND (cardinality(%s::text[]) = 0 OR c.polarity = ANY(%s::text[]))
               AND (cardinality(%s::text[]) = 0 OR e.entity_type = ANY(%s::text[]))
               AND (
                    (cardinality(%s::text[]) = 0 AND cardinality(%s::text[]) = 0)
                    OR c.aspect = ANY(%s::text[])
                    OR c.mood && %s::text[]
                    OR c.vibe && %s::text[]
                    OR c.activity && %s::text[]
               )
          ORDER BY c.destination_id,
                   c.source_quality DESC,
                   c.publish_date DESC NULLS LAST,
                   c.claim_id
        """
        with self._psycopg.connect(self.database_url) as connection:
            with connection.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute(
                    query,
                    (
                        destination_ids,
                        list(polarities),
                        list(polarities),
                        list(entity_types),
                        list(entity_types),
                        aspects,
                        list(tag_ids),
                        aspects,
                        list(tag_ids),
                        list(tag_ids),
                        list(tag_ids),
                    ),
                )
                seen_authors: dict[str, set[str]] = {
                    destination_id: set() for destination_id in destination_ids
                }
                for row in cursor.fetchall():
                    bucket = result[row["destination_id"]]
                    materialized = dict(row)
                    author_key = _claim_author_key(materialized)
                    if author_key in seen_authors[row["destination_id"]]:
                        continue
                    if len(bucket) >= per_destination:
                        continue
                    seen_authors[row["destination_id"]].add(author_key)
                    bucket.append(materialized)
        return result

    def get_travel_rows(self, destination_ids: list[str]) -> list[dict[str, Any]]:
        if not destination_ids:
            return []
        with self._psycopg.connect(self.database_url) as connection:
            with connection.cursor(row_factory=self._psycopg.rows.dict_row) as cursor:
                cursor.execute(
                    "SELECT * FROM travel_matrix WHERE destination_id = ANY(%s)",
                    (destination_ids,),
                )
                return [dict(row) for row in cursor.fetchall()]


def build_repository(data_dir: Path | None = None) -> RecommendationRepository:
    database_url = str(ENV_VALUES.get("DATABASE_URL") or "").strip()
    if database_url:
        return PostgresRecommendationRepository(database_url)
    return JsonlRecommendationRepository(data_dir or DEMO_DATA_DIR)
