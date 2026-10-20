"""Aggregate operations over accounts."""

from pkg.models import Account, SavingsAccount


def total_balance(accounts: list[Account]) -> int:
    running: int = 0
    for index in range(len(accounts)):
        running += accounts[index].balance
    return running


def owners(accounts: list[Account]) -> list[str]:
    return [account.owner for account in accounts]


def rich_owners(accounts: list[Account], threshold: int) -> list[str]:
    return [account.owner for account in accounts if account.balance > threshold]


def accrue_all(accounts: list[SavingsAccount]) -> int:
    earned: int = 0
    for account in accounts:
        earned += account.accrue()
    return earned


def find(accounts: list[Account], owner: str) -> Account | None:
    for account in accounts:
        if account.owner == owner:
            return account
    return None
