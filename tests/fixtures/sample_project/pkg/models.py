"""Domain types for the sample project."""


class Account:
    """A bank account with an owner and a balance."""

    rate: float = 0.02

    def __init__(self, owner: str, balance: int = 0) -> None:
        self.owner = owner
        self.balance = balance

    def deposit(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount

    def withdraw(self, amount: int) -> None:
        if amount > self.balance:
            raise ValueError("insufficient funds")
        self.balance -= amount

    def describe(self) -> str:
        return f"{self.owner}: {self.balance}"


class SavingsAccount(Account):
    """An account that can accrue interest."""

    def accrue(self) -> int:
        earned: int = int(self.balance * self.rate)
        self.balance += earned
        return earned
