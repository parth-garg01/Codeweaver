"""Strings and lists in the subset every native target supports."""


def caesar(text: str, shift: int) -> str:
    out = ""
    for ch in text:
        if "a" <= ch <= "z":
            out += chr((ord(ch) - ord("a") + shift) % 26 + ord("a"))
        else:
            out += ch
    return out


def rle(text: str) -> str:
    if len(text) == 0:
        return ""
    out = ""
    run = 1
    for i in range(1, len(text)):
        if text[i] == text[i - 1]:
            run += 1
        else:
            out += text[i - 1] + str(run)
            run = 1
    return out + text[-1] + str(run)


def reverse_words(sentence: str) -> str:
    words = sentence.split(" ")
    words.reverse()
    return " ".join(words)


def histogram(values: list[int], buckets: int) -> list[int]:
    counts = [0] * buckets
    for v in values:
        counts[v % buckets] += 1
    return counts


class Matrix:
    def __init__(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self.data: list[float] = [0.0] * (rows * cols)

    def get(self, r: int, c: int) -> float:
        return self.data[r * self.cols + c]

    def set(self, r: int, c: int, v: float) -> None:
        self.data[r * self.cols + c] = v

    def mul(self, other: "Matrix") -> "Matrix":
        out = Matrix(self.rows, other.cols)
        for i in range(self.rows):
            for j in range(other.cols):
                acc = 0.0
                for k in range(self.cols):
                    acc += self.get(i, k) * other.get(k, j)
                out.set(i, j, acc)
        return out


def run() -> None:
    word = "programming language"
    print(word[3:7], word[:4], word[7:], word[-8:], word[::2], word[::-1], word[10:2:-2], word[100:])
    print(word.count("g"), word.count("ng"), word.count(""), word.find("lang"), word.find("xyz"))
    print(caesar("hello, world", 3), caesar(caesar("abc xyz", 5), 21), chr(65) + chr(122))
    print(rle("aaabccddddde"), rle("x"), rle(""))
    print(reverse_words("the quick brown fox"))
    print(histogram([3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5], 4))
    a = Matrix(2, 3)
    b = Matrix(3, 2)
    for i in range(2):
        for j in range(3):
            a.set(i, j, float(i + j))
            b.set(j, i, float(i * j) + 0.5)
    c = a.mul(b)
    print(c.get(0, 0), c.get(0, 1), c.get(1, 0), c.get(1, 1))
    print(f"{c.get(1, 1):8.3f}|{len(word):>4}|{word[:3]:^7}|")
    nums = [5, 3, 8, 1, 9, 2]
    nums.sort()
    print(nums, nums[1:-1], nums[::-1], max(nums), min(nums), sum(nums) / len(nums))
    total = 0
    for i, n in enumerate(nums, 1):
        total += i * n
    print("weighted", total)
