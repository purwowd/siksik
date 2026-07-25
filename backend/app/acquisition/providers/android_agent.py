from __future__ import annotations

from app.acquisition.contracts import (
    AcquisitionContext,
    AcquisitionResult,
    AndroidAgentRunner,
    ProviderKind,
)
from app.acquisition.errors import ErrorCategory, acquisition_error


class AndroidAgentProvider:
    kind = ProviderKind.ANDROID_AGENT

    def __init__(self, runner: AndroidAgentRunner | None) -> None:
        self._runner = runner

    @property
    def available(self) -> bool:
        return self._runner is not None

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        if self._runner is None:
            raise acquisition_error(
                ErrorCategory.AGENT_UNAVAILABLE,
                "Provider Android agent belum siap digunakan.",
            )
        result = await self._runner.acquire(context)
        if result.provider != self.kind:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Provider Android agent mengembalikan identitas provider yang tidak valid.",
            )
        return result
