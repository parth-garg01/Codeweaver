"""Integer algorithms that exercise Python arithmetic semantics."""

LIMIT = 50
SQUARES = [n * n for n in range(10)]


def fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def primes_below(limit: int) -> list[int]:
    sieve = [True] * limit
    found: list[int] = []
    for i in range(2, limit):
        if sieve[i]:
            found.append(i)
            for j in range(i * i, limit, i):
                sieve[j] = False
    return found


def mean(values: list[int]) -> float:
    if not values:
        raise ValueError("no values")
    return sum(values) / len(values)


def python_division(a: int, b: int) -> str:
    return f"{a // b} {a % b} {a / b}"


def find_first(values: list[int], target: int) -> int:
    for index in range(len(values)):
        if values[index] == target:
            break
    else:
        return -1
    return index


def collatz(n: int) -> int:
    steps = 0
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps


def bubble_sort(values: list[int]) -> list[int]:
    items = list(values)
    for i in range(len(items)):
        for j in range(len(items) - 1 - i):
            if items[j] > items[j + 1]:
                items[j], items[j + 1] = items[j + 1], items[j]
    return items


def words(text: str, minimum: int = 3) -> list[str]:
    return [word.upper() for word in text.split() if len(word) >= minimum]
