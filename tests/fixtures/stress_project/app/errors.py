"""Exceptions and control flow."""


class ValidationError(Exception):
    pass


class Inventory:
    def __init__(self) -> None:
        self.items: dict[str, int] = {}

    def add(self, name: str, qty: int) -> None:
        if qty <= 0:
            raise ValidationError("quantity must be positive")
        self.items[name] = self.items.get(name, 0) + qty

    def remove(self, name: str, qty: int) -> int:
        if name not in self.items:
            raise KeyError(name)
        if self.items[name] < qty:
            raise ValidationError("not enough " + name)
        self.items[name] -= qty
        return self.items[name]


def safe_div(a: int, b: int) -> int:
    try:
        return a // b
    except ZeroDivisionError:
        return -1


def parse_all(values: list[str]) -> list[int]:
    out: list[int] = []
    for v in values:
        try:
            out.append(int(v))
        except ValueError:
            out.append(0)
    return out


def with_finally(n: int) -> str:
    log = ""
    try:
        if n < 0:
            raise ValidationError("neg")
        log += "ok;"
    except ValidationError as e:
        log += "err:" + str(e) + ";"
    finally:
        log += "done"
    return log


def run() -> None:
    inv = Inventory()
    inv.add("apple", 5)
    inv.add("pear", 2)
    inv.add("apple", 3)
    print(inv.remove("apple", 4))
    try:
        inv.remove("pear", 10)
    except ValidationError as e:
        print("caught", e)
    try:
        inv.add("plum", 0)
    except ValidationError as e:
        print("caught", e)
    try:
        inv.remove("kiwi", 1)
    except KeyError:
        print("missing kiwi")
    print(safe_div(9, 2), safe_div(1, 0))
    print(parse_all(["1", "x", "-3", "4.5"]))
    print(with_finally(1), with_finally(-1))
    print(sorted(inv.items.items()))
