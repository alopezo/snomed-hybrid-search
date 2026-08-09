-- Índices. Ejecutar MANUALMENTE tras cargar datos (ETL) y poblar embeddings.
-- No van en el init automático porque crear índices antes de la carga masiva es lento.

-- Canal léxico (correr tras la ETL, Fase 1):
CREATE INDEX IF NOT EXISTS ix_desc_tsv     ON descriptions USING gin (term_tsv);
CREATE INDEX IF NOT EXISTS ix_desc_concept ON descriptions (concept_id);

-- Canal semántico (correr DESPUÉS de poblar embeddings, Fase 2):
CREATE INDEX IF NOT EXISTS ix_desc_emb
    ON descriptions USING hnsw (embedding vector_cosine_ops);

ANALYZE descriptions;
