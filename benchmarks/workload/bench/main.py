from bench.kernels import Body, collatz_longest, matmul_trace, sieve, simulate


def main() -> None:
    print("sieve", sieve(3000000))
    print("collatz", collatz_longest(300000))
    print("matmul", matmul_trace(120))
    bodies: list[Body] = []
    for i in range(200):
        bodies.append(Body(i * 0.5, i * -0.25, 0.0, 0.1, 1.0 + i % 3))
    print("simulate", round(simulate(bodies, 2000), 6))


if __name__ == "__main__":
    main()
