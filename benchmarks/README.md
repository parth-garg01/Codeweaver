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
Conversion: 300 modules, best of 3, validation off
  target       seconds  modules/s  lines out
  typescript     0.351        856      11715
  c              0.583        515      22219
  cpp            0.539        556      21319
  java           0.572        524      15611
  (8682 lines of Python in)

Runtime: benchmarks/workload, best of 3
  target       seconds  speed-up  output
  python         1.159      1.0x  reference (3.12.16)
  c              0.037     31.4x  identical
  cpp            0.081     14.3x  identical
  java           0.219      5.3x  identical
```

Java's figure includes JVM start-up, which is most of its time at this size.
C++ objects are reference counted (`shared_ptr`), which keeps Python's
aliasing but costs a little on every object access, and indexing outside
provably safe loops is bounds checked; the CMake option
`-DCODEWEAVER_UNCHECKED=ON` removes those checks.
