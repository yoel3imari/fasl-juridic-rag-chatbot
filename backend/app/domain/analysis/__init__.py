"""Analysis package: rule-based matter analysis (task 8)."""

from app.domain.analysis.engine import build_analysis
from app.domain.analysis.glossary import load_glossary, match_terms

__all__ = ["build_analysis", "load_glossary", "match_terms"]
