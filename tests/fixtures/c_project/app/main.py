from app.algo import LIMIT, SQUARES, bubble_sort, collatz, fib, find_first, mean, primes_below, python_division, words
from app.shapes import Circle, Rect, Shape, Square, total_area


def main() -> None:
    print("fib", fib(30), fib(90))
    primes = primes_below(LIMIT)
    print("primes", primes, len(primes))
    print("mean", mean(primes))
    print("division", python_division(-7, 2), python_division(7, -2))
    print("squares", SQUARES[-3:], SQUARES[::-3])
    print("find", find_first(primes, 13), find_first(primes, 4))
    print("collatz", collatz(27))
    print("sorted", bubble_sort([5, -1, 3, 3, 0, 12, -8]))
    print("words", words("the quick brown fox jumps over a lazy dog"), words("a bb ccc", minimum=2))
    shapes: list[Shape] = [Circle(1.5), Rect(2.0, 3.5), Square(4.0)]
    for shape in shapes:
        print(shape.describe())
    print("total", round(total_area(shapes), 3))
    names = [shape.name for shape in shapes]
    print(", ".join(names), "square" in names, 1.0 / 3, 1e20, 2.5e-7)
    text = "hello"
    reversed_text = ""
    for char in text:
        reversed_text = char + reversed_text
    print(reversed_text, text[-1], len(text), str(True), 10**3 // 7)
    print(mean([]))


if __name__ == "__main__":
    main()
