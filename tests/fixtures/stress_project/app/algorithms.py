"""Algorithms and integer semantics."""


def gcd(a: int, b: int) -> int:
    while b != 0:
        a, b = b, a % b
    return a


def quicksort(items: list[int]) -> list[int]:
    if len(items) <= 1:
        return items
    pivot = items[len(items) // 2]
    left = [x for x in items if x < pivot]
    mid = [x for x in items if x == pivot]
    right = [x for x in items if x > pivot]
    return quicksort(left) + mid + quicksort(right)


def binary_search(items: list[int], target: int) -> int:
    lo = 0
    hi = len(items) - 1
    while lo <= hi:
        m = (lo + hi) // 2
        if items[m] == target:
            return m
        if items[m] < target:
            lo = m + 1
        else:
            hi = m - 1
    return -1


def collatz_len(n: int) -> int:
    steps = 0
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        steps += 1
    return steps


def is_prime(n: int) -> bool:
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True


def power(base: int, exp: int) -> int:
    result = 1
    for _ in range(exp):
        result *= base
    return result


def run() -> None:
    print("gcd", gcd(1071, 462), gcd(17, 5), gcd(0, 9))
    data = [38, 27, 43, 3, 9, 82, 10, -4, 27, 0]
    s = quicksort(data)
    print("sorted", s)
    print("search", binary_search(s, 43), binary_search(s, 44))
    print("collatz", collatz_len(27), max(collatz_len(n) for n in range(1, 1000)))
    print("primes", [n for n in range(60) if is_prime(n)])
    print("pow", power(2, 40), power(3, 20), power(-2, 7))
    print("divmod", -7 // 2, -7 % 2, 7 // -2, 7 % -2, -7 // -2, 17 % 5)
    print("float", 7 / 2, -7 / 2, 1 / 3, 2.0 ** 0.5, round(2.675, 2), abs(-3), abs(-2.5))
    print("minmax", min(3, 1, 2), max(data), min(data), sum(data), len(data))
    total = 0
    for i in range(10, 0, -2):
        if i == 4:
            continue
        if i == 2:
            break
        total += i
    print("loop", total)
    acc = 0
    i = 0
    while True:
        i += 1
        if i > 100:
            break
        if i % 3 == 0 or i % 5 == 0:
            acc += i
    print("euler1", acc)
    print("bools", 3 > 2 and 2 > 1, not True, 1 == 1.0, 5 != 5)
