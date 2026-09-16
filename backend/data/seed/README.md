# Seed documents (manual import)

Place curated PDFs/text here. This directory is the manual-import inbox for the
pre-embedded authority library. Files are NOT auto-downloaded; a curator copies
them here and lists them in `backend/data/library-manifest.json`, then runs:

    cd backend && uv run python -m app.library.seeder

Edition rule (SGG Bulletin Officiel): the Arabic general edition is authoritative.
French files must use edition `fr-translation` and are never presented as
authoritative without that label.
