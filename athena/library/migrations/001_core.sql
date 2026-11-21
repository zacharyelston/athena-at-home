-- SPDX-License-Identifier: GPL-3.0-or-later
-- Athena@Home library schema, v1. PostgreSQL 14 or later (ADR 0001).
--
-- The truth is the append-only `assertion` table: who said what about which record, when, from what source.
-- The other tables hold current state and are updated in the same transaction as the assertions that change
-- them, so current state can always be rebuilt from assertions. Text rows are append-only too.
-- Everything is private by default: visibility is 'private' on collections and NULL (inherit) below them.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE visibility AS ENUM ('private', 'members', 'public');

-- Provenance -------------------------------------------------------------------------------------------------

CREATE TABLE assertion (
    id           bigserial PRIMARY KEY,
    subject_type text        NOT NULL CHECK (subject_type IN ('collection', 'series', 'item', 'page', 'policy')),
    subject_id   text        NOT NULL,
    predicate    text        NOT NULL,          -- e.g. item.title, item.date, page.text, visibility
    value        jsonb       NOT NULL,
    source       text        NOT NULL,          -- capture:<station>, ocr:<engine>, person:<login>, config, policy:<name>
    asserted_at  timestamptz NOT NULL DEFAULT now(),
    supersedes   bigint      REFERENCES assertion (id)
);
CREATE INDEX assertion_subject ON assertion (subject_type, subject_id, predicate, id);

CREATE FUNCTION athena_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER assertion_append_only BEFORE UPDATE OR DELETE ON assertion
    FOR EACH ROW EXECUTE FUNCTION athena_append_only();
CREATE TRIGGER assertion_no_truncate BEFORE TRUNCATE ON assertion
    FOR EACH STATEMENT EXECUTE FUNCTION athena_append_only();

-- Arrangement ------------------------------------------------------------------------------------------------

CREATE TABLE collection (
    slug        text PRIMARY KEY,
    title       text       NOT NULL,
    description text       NOT NULL DEFAULT '',
    sort        integer    NOT NULL DEFAULT 0,
    visibility  visibility NOT NULL DEFAULT 'private'
);

CREATE TABLE series (
    slug        text PRIMARY KEY,
    collection  text       NOT NULL REFERENCES collection (slug),
    title       text       NOT NULL,
    description text       NOT NULL DEFAULT '',
    sort        integer    NOT NULL DEFAULT 0,
    visibility  visibility           -- NULL: inherit from the collection
);

CREATE TABLE item (
    id                 text PRIMARY KEY,                 -- the capture bundle's id (a ULID)
    collection         text REFERENCES collection (slug), -- NULL with series NULL: unsorted
    series             text REFERENCES series (slug),
    claimed_collection text        NOT NULL,              -- what the bundle said, kept even when unknown here
    claimed_series     text        NOT NULL,
    title              text        NOT NULL DEFAULT '',
    date_edtf          text,
    date_earliest      date,
    date_latest        date,
    type               text,
    creator            text        NOT NULL DEFAULT '',
    physical           jsonb       NOT NULL DEFAULT '{}',
    language           text,
    tags               text[]      NOT NULL DEFAULT '{}',
    notes              text        NOT NULL DEFAULT '',
    extra              jsonb       NOT NULL DEFAULT '{}', -- deployment-specific fields
    rights             jsonb       NOT NULL DEFAULT '{}',
    status             text        NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'ocr_done', 'reviewed')),
    visibility         visibility,                        -- NULL: inherit
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    date_range         daterange GENERATED ALWAYS AS (
        CASE WHEN date_earliest IS NULL AND date_latest IS NULL THEN NULL
             ELSE daterange(date_earliest, date_latest, '[]') END) STORED,
    search             tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(creator, '') || ' ' || coalesce(notes, '')), 'C')) STORED
);
CREATE INDEX item_series ON item (series);
CREATE INDEX item_status ON item (status);
CREATE INDEX item_search ON item USING gin (search);
CREATE INDEX item_dates ON item USING gist (date_range);
CREATE INDEX item_title_trgm ON item USING gin (title gin_trgm_ops);

CREATE TABLE page (
    id              text PRIMARY KEY,                      -- <item id>/<n>
    item            text        NOT NULL REFERENCES item (id),
    n               integer     NOT NULL CHECK (n >= 1),
    side            text        NOT NULL,
    kind            text        NOT NULL,
    original_sha256 text        NOT NULL,                  -- the raw master
    original_ext    text        NOT NULL,
    page_sha256     text        NOT NULL,                  -- cropped and deskewed page image
    page_ext        text        NOT NULL,
    ocr_sha256      text,                                  -- OCR-sized derivative (regenerable)
    width           integer     NOT NULL,
    height          integer     NOT NULL,
    ppi             real,
    ppi_source      text        NOT NULL,
    has_gps         boolean     NOT NULL DEFAULT false,
    quality         jsonb       NOT NULL DEFAULT '{}',
    visibility      visibility,                            -- NULL: inherit from the item
    redaction       jsonb       NOT NULL DEFAULT '[]',
    UNIQUE (item, n)
);

-- Text: every engine run and every human correction kept -----------------------------------------------------

CREATE TABLE text (
    id             bigserial PRIMARY KEY,
    page           text        NOT NULL REFERENCES page (id),
    tier           smallint    NOT NULL CHECK (tier IN (0, 1, 2)),  -- 0 quick (Apple Vision), 1 OCR, 2 a person
    engine         text        NOT NULL,
    engine_version text        NOT NULL DEFAULT '',
    tiles          integer,
    markdown       text        NOT NULL DEFAULT '',
    plain          text        NOT NULL,
    looped         boolean     NOT NULL DEFAULT false,
    hit_cap        boolean     NOT NULL DEFAULT false,
    source         text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    search         tsvector GENERATED ALWAYS AS (to_tsvector('english', plain)) STORED
);
CREATE INDEX text_page ON text (page, tier, id);
CREATE INDEX text_search ON text USING gin (search);
CREATE TRIGGER text_append_only BEFORE UPDATE OR DELETE ON text
    FOR EACH ROW EXECUTE FUNCTION athena_append_only();

-- The text of record per page: a person's correction, else OCR, else tier 0; newest first within a tier.
CREATE VIEW page_text_current AS
    SELECT DISTINCT ON (page) * FROM text ORDER BY page, tier DESC, id DESC;

-- Capture provenance -----------------------------------------------------------------------------------------

CREATE TABLE capture (
    item          text PRIMARY KEY REFERENCES item (id),
    station       text        NOT NULL,
    source        text        NOT NULL,
    device        jsonb       NOT NULL DEFAULT '{}',
    operator      text,
    created_at    timestamptz NOT NULL,
    ingested_at   timestamptz NOT NULL DEFAULT now(),
    slip_sha256   text,
    slip_ext      text,
    warnings      jsonb       NOT NULL DEFAULT '[]',
    manifest      jsonb       NOT NULL
);

-- Visibility policies (logic: athena.visibility, D11) ---------------------------------------------------------

CREATE TABLE policy (
    name    text PRIMARY KEY,
    kind    text       NOT NULL,
    cap     visibility NOT NULL,
    config  jsonb      NOT NULL DEFAULT '{}',
    enabled boolean    NOT NULL DEFAULT true
);

-- OCR failures for the review queue --------------------------------------------------------------------------

CREATE TABLE ocr_failure (
    id          bigserial PRIMARY KEY,
    page        text        NOT NULL REFERENCES page (id),
    reason      text        NOT NULL,
    source      text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE INDEX ocr_failure_open ON ocr_failure (page) WHERE resolved_at IS NULL;

-- Transactional outbox → library.events ----------------------------------------------------------------------

CREATE TABLE outbox (
    id           bigserial PRIMARY KEY,
    subject      text        NOT NULL,
    payload      jsonb       NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
CREATE INDEX outbox_pending ON outbox (id) WHERE published_at IS NULL;
