"""Entry point."""

from app.algo import InsufficientData, fib, find_first, mean, primes_below, python_division, safe_ratio, word_counts
from app.shapes import Circle, Rect, Shape, total_area


def summarize(shapes: list[Shape]) -> list[str]:
    return [shape.describe() for shape in shapes if shape.area() > 1]


if __name__ == "__main__":
    shapes: list[Shape] = [Circle(1.5), Rect(2.0, 3.0), Rect(0.5, 0.5)]
    for line in summarize(shapes):
        print(line)
    print("total", round(total_area(shapes), 3))
    print("fib", fib(30), fib(90))
    primes = primes_below(50)
    print("primes", primes, len(primes))
    print("last", primes[-1], primes[1:4], primes[::-5])
    print("mean", mean(primes))
    print("division", python_division(-7, 2), python_division(7, -2))
    counts = word_counts("the cat and The hat and THE bat")
    for word in sorted(counts):
        print(word, counts[word])
    print("find", find_first(primes, 13), find_first(primes, 4))
    print(safe_ratio(1, 4), safe_ratio(1, 0))
    try:
        mean([])
    except InsufficientData as error:
        print("caught", error)
    names = {shape.name for shape in shapes}
    print("kinds", len(names), "rect" in names, "oval" in names)
    squares = {n: n * n for n in range(5)}
    print("squares", squares)
    print("flags", True, None, 2.5, 1e20, 0.1 + 0.2, -0.0)
