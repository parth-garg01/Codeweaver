"""The other half of a deliberate import cycle."""

from pkg.cycle_a import alpha


def beta(n: int) -> int:
    if n <= 0:
        return 0
    return alpha(n - 1)
