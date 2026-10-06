from __future__ import annotations

from typing import Any

from question_bank.training_criteria import gateway_parallel_limit

from .manager import JobContext


class CancellationAwareGateway:
    def __init__(self, gateway: Any, context: JobContext) -> None:
        self.gateway = gateway
        self.context = context

    @property
    def max_parallel_requests(self) -> int:
        return gateway_parallel_limit(self.gateway)

    def analyze(self, *args: Any, **kwargs: Any) -> Any:
        self.context.raise_if_cancelled()
        response = self.gateway.analyze(*args, **kwargs)
        self.context.raise_if_cancelled()
        return response
