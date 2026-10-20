"""Half of a deliberate import cycle."""

from pkg.cycle_b import beta


def alpha(n: int) -> int:
    if n <= 0:
        return 0
    return beta(n - 1)
