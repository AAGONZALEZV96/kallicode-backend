from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Invoice:
    id: str
    amount: Decimal
    paid: bool = False


class BillingService:
    def __init__(self, tax_rate: Decimal = Decimal("0.19")):
        self.tax_rate = tax_rate

    def calculate_total(self, invoice: Invoice) -> Decimal:
        return invoice.amount * (1 + self.tax_rate)

    @staticmethod
    def mark_as_paid(invoice: Invoice) -> Invoice:
        invoice.paid = True
        return invoice


def format_currency(amount: Decimal) -> str:
    return f"${amount:,.2f}"
