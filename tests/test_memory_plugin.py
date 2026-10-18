from __future__ import annotations

import math

from aether.plugins.memory_plugin import LocalHashEmbeddingFunction


def test_local_memory_embedding_is_deterministic_normalized_and_offline() -> None:
    embedding = LocalHashEmbeddingFunction()
    first = embedding.embed("Aether remembers the user's preferred model")
    second = embedding.embed("Aether remembers the user's preferred model")

    assert first == second
    assert len(first) == 384
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)
    assert embedding.embed_query(["Aether remembers"])[0] == embedding.embed("Aether remembers")
    assert LocalHashEmbeddingFunction.build_from_config(embedding.get_config()).dimensions == 384
