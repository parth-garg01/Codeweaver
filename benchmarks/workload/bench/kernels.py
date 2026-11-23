"""CPU-bound kernels written in the subset every native target supports."""


class Body:
    def __init__(self, x: float, y: float, vx: float, vy: float, mass: float) -> None:
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.mass = mass


def sieve(limit: int) -> int:
    flags = [True] * limit
    count = 0
    for i in range(2, limit):
        if flags[i]:
            count += 1
            for j in range(i * i, limit, i):
                flags[j] = False
    return count


def collatz_longest(limit: int) -> int:
    best = 0
    best_start = 1
    for start in range(1, limit):
        n = start
        steps = 0
        while n != 1:
            if n % 2 == 0:
                n = n // 2
            else:
                n = 3 * n + 1
            steps += 1
        if steps > best:
            best = steps
            best_start = start
    return best_start


def matmul_trace(size: int) -> float:
    a: list[float] = []
    b: list[float] = []
    for i in range(size * size):
        a.append((i % 7) * 0.5)
        b.append((i % 5) * 0.25)
    c = [0.0] * (size * size)
    for i in range(size):
        for k in range(size):
            aik = a[i * size + k]
            for j in range(size):
                c[i * size + j] += aik * b[k * size + j]
    trace = 0.0
    for i in range(size):
        trace += c[i * size + i]
    return trace


def simulate(bodies: list[Body], steps: int) -> float:
    for _ in range(steps):
        for body in bodies:
            body.vx -= body.x * 0.001 * body.mass
            body.vy -= body.y * 0.001 * body.mass
        for body in bodies:
            body.x += body.vx * 0.01
            body.y += body.vy * 0.01
    energy = 0.0
    for body in bodies:
        energy += 0.5 * body.mass * (body.vx * body.vx + body.vy * body.vy)
    return energy
