"""Heavier CPU workload: n-queens, DP and simulation."""


def queens(n: int) -> int:
    cols = [False] * n
    d1 = [False] * (2 * n)
    d2 = [False] * (2 * n)
    return place(0, n, cols, d1, d2)


def place(row: int, n: int, cols: list[bool], d1: list[bool], d2: list[bool]) -> int:
    if row == n:
        return 1
    count = 0
    for c in range(n):
        if not cols[c] and not d1[row + c] and not d2[row - c + n]:
            cols[c] = True
            d1[row + c] = True
            d2[row - c + n] = True
            count += place(row + 1, n, cols, d1, d2)
            cols[c] = False
            d1[row + c] = False
            d2[row - c + n] = False
    return count


def lcs(a: str, b: str) -> int:
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[len(a)][len(b)]


class Particle:
    def __init__(self, x: float, v: float) -> None:
        self.x = x
        self.v = v

    def step(self, dt: float) -> None:
        self.v -= 9.8 * dt
        self.x += self.v * dt
        if self.x < 0:
            self.x = -self.x
            self.v = -self.v * 0.9


def simulate(n: int, steps: int) -> float:
    ps = [Particle(float(i), 0.0) for i in range(n)]
    for _ in range(steps):
        for p in ps:
            p.step(0.001)
    total = 0.0
    for p in ps:
        total += p.x
    return total


def lcg_sum(n: int) -> int:
    x = 12345
    s = 0
    for _ in range(n):
        x = (x * 1103515245 + 12345) % 2147483648
        s = (s + x) % 1000000007
    return s


def run() -> None:
    print("queens", queens(9))
    print("lcs", lcs("ACCGGTCGAGTGCGCGGAAGCCGGCCGAA" * 6, "GTCGTTCGGAATGCCGTTGCTCTGTAAA" * 6))
    print("sim", round(simulate(50, 500), 4))
    print("lcg", lcg_sum(200000))
