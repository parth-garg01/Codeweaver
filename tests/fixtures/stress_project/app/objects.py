"""Object orientation, dispatch and aliasing."""


class Animal:
    def __init__(self, name: str, legs: int) -> None:
        self.name = name
        self.legs = legs

    def sound(self) -> str:
        return "..."

    def describe(self) -> str:
        return f"{self.name} has {self.legs} legs and says {self.sound()}"


class Dog(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name, 4)
        self.tricks: list[str] = []

    def sound(self) -> str:
        return "woof"

    def learn(self, trick: str) -> None:
        self.tricks.append(trick)


class Puppy(Dog):
    def sound(self) -> str:
        return "yip " + super().sound()


class Bird(Animal):
    def __init__(self, name: str, can_fly: bool) -> None:
        super().__init__(name, 2)
        self.can_fly = can_fly

    def sound(self) -> str:
        if self.can_fly:
            return "tweet"
        return "squawk"


class Counter:
    def __init__(self) -> None:
        self.count = 0

    def bump(self, by: int) -> int:
        self.count += by
        return self.count


class Account:
    def __init__(self, owner: str, balance: float) -> None:
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: float) -> None:
        self.balance += amount

    def transfer(self, other: "Account", amount: float) -> bool:
        if amount > self.balance:
            return False
        self.balance -= amount
        other.deposit(amount)
        return True


def bump_all(counters: list[Counter]) -> int:
    total = 0
    for c in counters:
        total += c.bump(2)
    return total


def run() -> None:
    zoo: list[Animal] = [Dog("rex"), Puppy("bit"), Bird("tweety", True), Bird("pingu", False)]
    for a in zoo:
        print(a.describe())
    d = Dog("max")
    d.learn("sit")
    d.learn("roll")
    print(d.name, d.tricks, len(d.tricks))
    c = Counter()
    alias = c
    alias.bump(5)
    print("alias", c.count, c.bump(1))
    cs = [c, Counter(), c]
    print("bump_all", bump_all(cs), c.count)
    a1 = Account("ann", 100.0)
    a2 = Account("bob", 5.5)
    print(a1.transfer(a2, 30.25), a1.transfer(a2, 1000.0))
    print(a1.owner, a1.balance, a2.owner, a2.balance)
    legs = 0
    for a in zoo:
        legs += a.legs
    print("legs", legs)
