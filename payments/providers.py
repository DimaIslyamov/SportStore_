from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4


@dataclass(frozen=True)
class ProviderResult:
    succeeded: bool
    external_id: str


class PaymentProvider(ABC):
    @abstractmethod
    def pay(self, *, amount: Decimal, currency: str, outcome: str) -> ProviderResult:
        raise NotImplementedError


class FakePaymentProvider(PaymentProvider):
    def pay(self, *, amount: Decimal, currency: str, outcome: str) -> ProviderResult:
        if outcome not in {"success", "failed"}:
            raise ValueError("Unknown fake payment outcome.")
        return ProviderResult(
            succeeded=outcome == "success",
            external_id=f"fake_{uuid4().hex}",
        )
