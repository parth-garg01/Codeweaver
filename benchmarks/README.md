# Benchmarks

```bash
PYTHONPATH=src python3 benchmarks/run.py             # conversion and runtime
PYTHONPATH=src python3 benchmarks/run.py --convert --modules 1000
PYTHONPATH=src python3 benchmarks/run.py --runtime --repeat 5
```

## Conversion speed

`run.py` synthesises a project of `--modules` modules (classes in a deep
inheritance chain, typed functions, loops and comprehensions) and reports the
best wall-clock time of the whole pipeline (scan, parse, transforms, type
inference and generation) for each target, with compiler validation switched
off so that only CodeWeaver's own time is measured.

## Runtime speed

`workload/` is a CPU-bound program written in the subset all native targets
support: a prime sieve, a Collatz search, a matrix multiply and a small
object-heavy simulation. `run.py` translates it, builds each target with `-O3`
(or `javac`), checks that the output is **identical** to CPython's (otherwise
it fails), and reports the best of `--repeat` runs, process start-up included.

## Reference results

Apple M5 (arm64), CPython 3.12, Apple clang 21, OpenJDK 21. Your numbers
will differ. Rerun the benchmark rather than quoting these.

```text
Conversion: 300 modules, best of 5, validation off
  target       seconds  modules/s  lines out
  typescript     0.354        847      11715
  c              0.607        494      22224
  cpp            0.551        545      21595
  java           0.585        513      15611
  (8682 lines of Python in)

Runtime: benchmarks/workload, best of 5
  target       seconds  speed-up  output
  python         1.175      1.0x  reference (3.12.16)
  c              0.050     23.7x  identical
  cpp            0.082     14.3x  identical
  java           0.234      5.0x  identical
```

Java's figure includes JVM start-up, which is most of its time at this size.
C++ objects are reference counted (`shared_ptr`), which keeps Python's
aliasing but costs a little on every object access, and indexing outside
provably safe loops is bounds checked.

int `+`, `-`, `*` and `**` are overflow checked on every native target, so a
result Python would grow past 64 bits raises `OverflowError` instead of
printing a wrapped number. The check is one flag test per operation. It costs
C about a third on this workload, because the checked `i * size + j` index
arithmetic keeps clang from vectorising the matrix multiply (0.036 s
unchecked, 0.050 s checked). C++ and Java show no measurable cost. The CMake
option `-DCODEWEAVER_UNCHECKED=ON` (or `-DCW_UNCHECKED` by hand) removes the
overflow checks in C and C++, and the index bounds checks in C++, for maximum
speed once a program is known to stay in range.

Builds use `-ffp-contract=off`, and the runtime headers set
`#pragma STDC FP_CONTRACT OFF`. Without that, clang fuses `x += v * dt` into
one FMA instruction and the last digits of float results stop matching
CPython. The pragma costs nothing measurable here.
