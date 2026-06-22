from __future__ import annotations

import secrets
from typing import Sequence, TypeVar

T = TypeVar("T")

_rand = secrets.SystemRandom()


def sample_winners(pool: Sequence[T], k: int) -> list[T]:
    if k <= 0 or not pool:
        return []
    k = min(k, len(pool))
    return _rand.sample(list(pool), k)
