"""Dual-domain hybrid search (task 5): matter_evidence vs legal_authorities.

Each domain is dense (CrispEmbed, model from settings) + lexical sparse fused with RRF.
Matter queries always pre-filter matter_id; both-domain responses keep
SEPARATE labeled lists, never one merged list.
"""

from __future__ import annotations
