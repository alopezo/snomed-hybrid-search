-- Esquema base. Se ejecuta automáticamente en el PRIMER arranque del contenedor
-- (docker-entrypoint-initdb.d) cuando la BD está vacía.
-- Los índices GIN/HNSW NO van aquí: se crean tras cargar datos (ver sql/02_indexes.sql).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Accent- and case-insensitive text-search config (unaccent + simple; NO stemming).
-- Used by BOTH indexing (to_tsvector) and querying (to_tsquery) so normalization is identical
-- on both sides. This is why "riñón" / "rinon" and "Diabético" / "diabetico" match.
DROP TEXT SEARCH CONFIGURATION IF EXISTS unaccent_simple;
CREATE TEXT SEARCH CONFIGURATION unaccent_simple (COPY = simple);
ALTER TEXT SEARCH CONFIGURATION unaccent_simple
    ALTER MAPPING FOR asciiword, asciihword, hword_asciipart, word, hword, hword_part
    WITH unaccent, simple;

CREATE TABLE IF NOT EXISTS descriptions (
    id           BIGINT PRIMARY KEY,        -- descriptionId (RF2)
    concept_id   BIGINT NOT NULL,           -- conceptId
    term         TEXT   NOT NULL,           -- término original
    term_norm    TEXT   NOT NULL,           -- unaccent(lower(term))
    type_id      BIGINT NOT NULL,           -- 900000000000013009 sinónimo / 900000000000003001 FSN
    semantic_tag TEXT,                       -- p.ej. "disorder", "finding" (del FSN)
    pref_us      BOOLEAN NOT NULL DEFAULT false,
    pref_gb      BOOLEAN NOT NULL DEFAULT false,
    term_tsv     tsvector,                   -- canal léxico (multi-prefix ignore-order)
    embedding    vector(768)                 -- canal semántico (BioLORD-2023-M)
);

COMMENT ON TABLE descriptions IS 'Descripciones activas de SNOMED CT para búsqueda híbrida léxico+semántica';
