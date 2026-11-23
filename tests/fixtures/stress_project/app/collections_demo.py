"""Strings, dicts and collections."""


def word_freq(text: str) -> dict[str, int]:
    freq: dict[str, int] = {}
    for w in text.lower().split():
        freq[w] = freq.get(w, 0) + 1
    return freq


def caesar(text: str, shift: int) -> str:
    out = ""
    for ch in text:
        if "a" <= ch <= "z":
            out += chr((ord(ch) - ord("a") + shift) % 26 + ord("a"))
        else:
            out += ch
    return out


def is_palindrome(s: str) -> bool:
    cleaned = "".join([c for c in s.lower() if c.isalnum()])
    return cleaned == cleaned[::-1]


def matrix(n: int) -> list[list[int]]:
    rows: list[list[int]] = []
    for i in range(n):
        row: list[int] = []
        for j in range(n):
            row.append(i * n + j)
        rows.append(row)
    return rows


def run() -> None:
    f = word_freq("the quick brown fox jumps over the lazy dog The End")
    for k in sorted(f):
        print(k, f[k])
    print("has", "fox" in f, "cat" in f, len(f))
    print(caesar("hello, world", 3), caesar(caesar("abc xyz", 5), 21))
    print(is_palindrome("A man, a plan, a canal: Panama"), is_palindrome("hello"))
    s = "  Hello World  "
    print("[" + s.strip() + "]", s.upper(), s.replace("l", "L"), s.strip().startswith("He"))
    print("-".join(["a", "b", "c"]), "a,b,,c".split(","), len("hello"))
    word = "programming"
    print(word[0], word[-1], word[3:7], word[:4], word[7:], word.find("gram"), word.count("m"))
    m = matrix(4)
    print(m[2][3], m[-1], [r[0] for r in m])
    stack: list[int] = []
    for i in range(6):
        stack.append(i * i)
    top = stack.pop()
    stack.insert(0, 99)
    print("stack", stack, top, stack.index(4))
    pairs = list(zip([1, 2, 3], ["x", "y", "z"]))
    for i, (n, ch) in enumerate(pairs):
        print(i, n, ch)
    sq = {n: n * n for n in range(1, 6)}
    keys = list(sq.keys())
    print("dict", sq, keys, sum(sq.values()))
    uniq = {x % 4 for x in range(20)}
    print("set", sorted(uniq), 3 in uniq)
    name = "Ada"
    pi = 3.14159
    print(f"{name}: {pi:.2f} {42:5d}|{name:>6}|{name:<6}|")
    print(str(12) + str(3.5), int("42") + 1, float("2.5") * 2)
