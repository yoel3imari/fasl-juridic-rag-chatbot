"""Ingestion orchestration package: pipeline, OCR adapter and indexer.

The pure rules (sectioning, classification, errors, schemas) moved to
`app.domain.ingestion`; text extraction moved to
`app.infrastructure.ingestion.extract`. The former schema re-exports were
removed in plan todo 13 — nothing imported them, and compatibility
re-export shims are forbidden. Import the new modules directly.
"""
