"""Dim-coupled sweep guard (plan todo 19): the lexical sparse vector stays 2048-dim.

`sparse.py` is frozen (TF-hash, model-independent); this test fails loudly if
anyone couples the sparse dim to the dense embedding dim (gate winner vs.
fallback model).
"""

from app.search.sparse import SPARSE_DIM, sparse_indices_values, sparse_vector


def test_sparse_dim_stays_2048():
    """SPARSE_DIM is 2048 regardless of the dense EMBEDDING_DIM."""
    from app.config import settings

    assert SPARSE_DIM == 2048
    assert settings.EMBEDDING_DIM != SPARSE_DIM


def test_sparse_indices_bounded_by_dim():
    """Every hashed index lands inside [0, 2048) with TF weights."""
    indices, values = sparse_indices_values("المادة الأولى test المادة الأولى")
    assert indices
    assert len(indices) == len(values)
    assert min(indices) >= 0
    assert max(indices) < 2048
    assert indices == sorted(indices)
    assert all(v >= 1.0 for v in values)


def test_sparse_vector_roundtrip_dim():
    """Qdrant SparseVector indices stay inside the frozen 2048-dim space."""
    vec = sparse_vector("test المادة الأولى")
    assert max(vec.indices) < 2048
    assert len(vec.indices) == len(vec.values)
