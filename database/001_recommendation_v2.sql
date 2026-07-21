BEGIN;

CREATE TABLE IF NOT EXISTS recommendation_entities (
    entity_id text PRIMARY KEY,
    legacy_poi_id text,
    entity_type text NOT NULL CHECK (entity_type IN ('destination', 'experience', 'service', 'transport_node')),
    parent_id text REFERENCES recommendation_entities(entity_id),
    name text NOT NULL,
    aliases text[] NOT NULL DEFAULT '{}',
    city text NOT NULL,
    province text NOT NULL CHECK (province IN ('上海', '江苏', '浙江')),
    category text NOT NULL DEFAULT '',
    longitude double precision,
    latitude double precision,
    map_poi_id text,
    standard_province text NOT NULL DEFAULT '',
    standard_city text NOT NULL DEFAULT '',
    standard_district text NOT NULL DEFAULT '',
    adcode text NOT NULL DEFAULT '',
    address text NOT NULL DEFAULT '',
    telephone text NOT NULL DEFAULT '',
    business_area text NOT NULL DEFAULT '',
    opening_hours text NOT NULL DEFAULT '',
    map_operational_status text NOT NULL DEFAULT 'unknown',
    map_match_confidence double precision,
    map_match_level text NOT NULL DEFAULT 'unknown',
    geocode_status text NOT NULL DEFAULT 'pending',
    map_review_status text NOT NULL DEFAULT 'review_required',
    map_checked_at timestamptz,
    map_source text NOT NULL DEFAULT '',
    map_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'unknown' CHECK (status IN ('active', 'inactive', 'unknown')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS standard_province text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS standard_city text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS standard_district text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS adcode text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS address text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS telephone text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS business_area text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS opening_hours text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_operational_status text NOT NULL DEFAULT 'unknown';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_match_confidence double precision;
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_match_level text NOT NULL DEFAULT 'unknown';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS geocode_status text NOT NULL DEFAULT 'pending';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_review_status text NOT NULL DEFAULT 'review_required';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_checked_at timestamptz;
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_source text NOT NULL DEFAULT '';
ALTER TABLE recommendation_entities ADD COLUMN IF NOT EXISTS map_payload jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS recommendation_entities_parent_idx
    ON recommendation_entities(parent_id);
CREATE INDEX IF NOT EXISTS recommendation_entities_lookup_idx
    ON recommendation_entities(entity_type, province, city, status);

CREATE TABLE IF NOT EXISTS destination_facts (
    destination_id text PRIMARY KEY REFERENCES recommendation_entities(entity_id) ON DELETE CASCADE,
    duration_min integer,
    duration_max integer,
    duration_source text,
    budget_min integer,
    budget_typical integer,
    budget_max integer,
    budget_basis text,
    budget_confidence text CHECK (budget_confidence IN ('高', '中', '低')),
    budget_filterable boolean NOT NULL DEFAULT false,
    requires_ferry boolean NOT NULL DEFAULT false,
    best_season text,
    operational_status text NOT NULL DEFAULT 'unknown',
    fact_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS travel_matrix (
    destination_id text NOT NULL REFERENCES recommendation_entities(entity_id) ON DELETE CASCADE,
    origin_city text NOT NULL,
    transport_mode text NOT NULL,
    travel_minutes integer,
    distance_m bigint,
    source text NOT NULL,
    confidence text NOT NULL DEFAULT '低' CHECK (confidence IN ('高', '中', '低')),
    requires_ferry boolean NOT NULL DEFAULT false,
    contains_ferry boolean NOT NULL DEFAULT false,
    note text NOT NULL DEFAULT '',
    failure_reason text NOT NULL DEFAULT '',
    partial_failure_reasons text[] NOT NULL DEFAULT '{}',
    raw_status jsonb NOT NULL DEFAULT '{}'::jsonb,
    route_estimate boolean NOT NULL DEFAULT true,
    origin_name text NOT NULL DEFAULT '',
    origin_type text NOT NULL DEFAULT '',
    door_to_door_min integer,
    door_to_door_typical integer,
    door_to_door_max integer,
    rail_segment_min integer,
    rail_segment_typical integer,
    rail_segment_max integer,
    access_egress_min integer,
    access_egress_typical integer,
    access_egress_max integer,
    railway_segments jsonb NOT NULL DEFAULT '[]'::jsonb,
    sample_dates text[] NOT NULL DEFAULT '{}',
    sample_times text[] NOT NULL DEFAULT '{}',
    planned_sample_count integer NOT NULL DEFAULT 0,
    route_sample_count integer NOT NULL DEFAULT 0,
    ferry_detection_sources text[] NOT NULL DEFAULT '{}',
    travel_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (destination_id, origin_city, transport_mode)
);

ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS distance_m bigint;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS contains_ferry boolean NOT NULL DEFAULT false;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS failure_reason text NOT NULL DEFAULT '';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS partial_failure_reasons text[] NOT NULL DEFAULT '{}';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS raw_status jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS route_estimate boolean NOT NULL DEFAULT true;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS origin_name text NOT NULL DEFAULT '';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS origin_type text NOT NULL DEFAULT '';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS door_to_door_min integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS door_to_door_typical integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS door_to_door_max integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS rail_segment_min integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS rail_segment_typical integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS rail_segment_max integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS access_egress_min integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS access_egress_typical integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS access_egress_max integer;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS railway_segments jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS sample_dates text[] NOT NULL DEFAULT '{}';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS sample_times text[] NOT NULL DEFAULT '{}';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS planned_sample_count integer NOT NULL DEFAULT 0;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS route_sample_count integer NOT NULL DEFAULT 0;
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS ferry_detection_sources text[] NOT NULL DEFAULT '{}';
ALTER TABLE travel_matrix ADD COLUMN IF NOT EXISTS travel_payload jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS travel_matrix_filter_idx
    ON travel_matrix(origin_city, transport_mode, travel_minutes);

CREATE TABLE IF NOT EXISTS ugc_evidence_claims (
    claim_id text PRIMARY KEY,
    evidence_id text NOT NULL,
    entity_id text NOT NULL REFERENCES recommendation_entities(entity_id) ON DELETE CASCADE,
    destination_id text REFERENCES recommendation_entities(entity_id) ON DELETE CASCADE,
    note_id text NOT NULL,
    aspect text NOT NULL,
    polarity text NOT NULL CHECK (polarity IN ('positive', 'negative', 'mixed', 'neutral')),
    claim text NOT NULL,
    key_quote text NOT NULL,
    mood text[] NOT NULL DEFAULT '{}',
    vibe text[] NOT NULL DEFAULT '{}',
    activity text[] NOT NULL DEFAULT '{}',
    conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
    author_hash text NOT NULL DEFAULT '',
    publish_date date,
    collected_date date,
    source_quality double precision NOT NULL CHECK (source_quality BETWEEN 0 AND 1),
    is_suspected_ad boolean NOT NULL DEFAULT false,
    source_url text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ugc_claims_destination_aspect_idx
    ON ugc_evidence_claims(destination_id, aspect, polarity, publish_date DESC);
CREATE INDEX IF NOT EXISTS ugc_claims_entity_idx
    ON ugc_evidence_claims(entity_id);

CREATE TABLE IF NOT EXISTS destination_profiles (
    destination_id text PRIMARY KEY REFERENCES recommendation_entities(entity_id) ON DELETE CASCADE,
    mood_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    vibe_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    activity_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    core_feeling text NOT NULL DEFAULT '',
    atmosphere text NOT NULL DEFAULT '',
    suitable_scenes jsonb NOT NULL DEFAULT '[]'::jsonb,
    activities jsonb NOT NULL DEFAULT '[]'::jsonb,
    limitations jsonb NOT NULL DEFAULT '[]'::jsonb,
    positive_evidence_count integer NOT NULL DEFAULT 0,
    limitation_evidence_count integer NOT NULL DEFAULT 0,
    evidence_quality double precision NOT NULL DEFAULT 0,
    freshness_score double precision NOT NULL DEFAULT 0,
    private_discovery_value double precision NOT NULL DEFAULT 0,
    source_count integer NOT NULL DEFAULT 0,
    profile_version text NOT NULL DEFAULT '2.1.0',
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE destination_profiles
    ADD COLUMN IF NOT EXISTS positive_evidence_count integer NOT NULL DEFAULT 0;
ALTER TABLE destination_profiles
    ADD COLUMN IF NOT EXISTS limitation_evidence_count integer NOT NULL DEFAULT 0;
ALTER TABLE destination_profiles
    ALTER COLUMN profile_version SET DEFAULT '2.1.0';

COMMIT;
