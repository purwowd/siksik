from __future__ import annotations

import logging

from app.acquisition.contracts import (
    AcquisitionContext,
    AcquisitionProvider,
    AcquisitionResult,
    AndroidAgentRunner,
    ProviderKind,
)
from app.acquisition.errors import ErrorCategory, acquisition_error
from app.acquisition.providers.android_agent import AndroidAgentProvider
from app.acquisition.providers.existing import (
    AndroidLegacyProvider,
    IOSProvider,
    SimulatorProvider,
    ZipUploadProvider,
)
from app.models.schemas import DeviceType

logger = logging.getLogger("siksik.acquisition.providers")


class AcquisitionProviderRegistry:
    def __init__(
        self,
        *,
        android_agent_enabled: bool = False,
        android_legacy_fallback: bool = True,
        agent_runner: AndroidAgentRunner | None = None,
    ) -> None:
        self._android_agent_enabled = android_agent_enabled
        self._android_legacy_fallback = android_legacy_fallback
        self._providers: dict[ProviderKind, AcquisitionProvider] = {
            ProviderKind.ANDROID_AGENT: AndroidAgentProvider(agent_runner),
            ProviderKind.ANDROID_LEGACY: AndroidLegacyProvider(),
            ProviderKind.IOS: IOSProvider(),
            ProviderKind.SIMULATOR: SimulatorProvider(),
            ProviderKind.ZIP_UPLOAD: ZipUploadProvider(),
        }

    def provider_for(self, context: AcquisitionContext) -> AcquisitionProvider:
        if context.archive is not None:
            return self._providers[ProviderKind.ZIP_UPLOAD]
        if (
            context.simulated
            or context.device_id.startswith("sim-")
            or context.device_type == DeviceType.SIMULATED
        ):
            return self._providers[ProviderKind.SIMULATOR]
        if context.device_type == DeviceType.IOS:
            return self._providers[ProviderKind.IOS]
        if context.device_type == DeviceType.ANDROID:
            if self._android_agent_enabled:
                agent = self._providers[ProviderKind.ANDROID_AGENT]
                if isinstance(agent, AndroidAgentProvider) and agent.available:
                    return agent
                if not self._android_legacy_fallback:
                    raise acquisition_error(
                        ErrorCategory.AGENT_UNAVAILABLE,
                        "Provider Android agent aktif tetapi runner belum tersedia.",
                    )
                logger.warning(
                    "android_agent_fallback",
                    extra={
                        "request_id": context.request_id,
                        "session_id": context.session_id,
                        "error_category": ErrorCategory.AGENT_UNAVAILABLE.value,
                        "fallback_provider": ProviderKind.ANDROID_LEGACY.value,
                    },
                )
            return self._providers[ProviderKind.ANDROID_LEGACY]
        raise acquisition_error(
            ErrorCategory.DEVICE_UNSUPPORTED,
            "Jenis perangkat tidak didukung oleh provider akuisisi.",
        )

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        provider = self.provider_for(context)
        logger.info(
            "acquisition_provider_selected",
            extra={
                "request_id": context.request_id,
                "session_id": context.session_id,
                "provider": provider.kind.value,
            },
        )
        return await provider.acquire(context)
