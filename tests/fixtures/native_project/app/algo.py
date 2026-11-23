"""Integer algorithms that exercise Python arithmetic semantics."""


class InsufficientData(ValueError):
    pass


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
        raise InsufficientData("no values")
    return sum(values) / len(values)


def python_division(a: int, b: int) -> str:
    return f"{a // b} {a % b} {a / b}"


def word_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return counts


def find_first(values: list[int], target: int) -> int:
    for index in range(len(values)):
        if values[index] == target:
            break
    else:
        return -1
    return index


def safe_ratio(a: int, b: int) -> str:
    try:
        result = a / b
    except ZeroDivisionError:
        return "undefined"
    else:
        return f"{result}"
    finally:
        print("ratio checked")
