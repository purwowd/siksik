from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.automation import AutomationScopeProgressV1

from app.acquisition.agent_client import (
    AutomationResultV1,
    INVENTORY_SOURCES,
    InventoryRunV1,
    PreprocessingRunV1,
    SelectionRunV1,
)
from app.acquisition.bootstrap_contracts import special_access_for_inventory_mode
from app.acquisition.contracts import AcquisitionContext, AcquisitionResult, ProviderKind
from app.acquisition.live_ingestion import live_selected_ingestor
from app.acquisition.direct_transfer import direct_crawl_transfer
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.runtime import AgentRuntimeSecrets
from app.acquisition.time_scope import build_time_scope
from app.models.schemas import SessionStatus
from app.core.config import settings
from app.selection.contracts import SelectionPolicyV1
from app.selection.policy import build_selection_policy, verify_policy_fingerprint
from app.selection.repository import selection_repository as default_selection_repository

logger = logging.getLogger("siksik.acquisition.android_agent")

INVENTORY_PAGE_LIMITS = {
    "public_whatsapp": 100,
    "public_telegram": 100,
    "media_store_image": 100,
    "media_store_video": 100,
    "media_store_audio": 100,
    "shared_storage_document": 100,
    "document_tree": 100,
    "sms_content_provider": 64,
    "contacts_content_provider": 100,
    "accessibility_visible_ui": 50,
    "notification_listener": 50,
}
SELECTION_CANDIDATE_PAGE_LIMIT = 50
LIVE_SELECTION_PAGE_LIMIT = 16
LIVE_ANALYSIS_BATCH_SIZE = 64
SOCIAL_SCOPE_ATTEMPTS = 4


class BootstrapService(Protocol):
    async def bootstrap(self, **kwargs): ...


class RuntimeRegistry(Protocol):
    async def get(self, session_id: str) -> AgentRuntimeSecrets: ...


class InventoryClient(Protocol):
    async def bootstrap(self, *args, **kwargs): ...
    async def start_inventory(self, *args, **kwargs): ...
    async def inventory_page(self, *args, **kwargs): ...
    async def inventory_status(self, *args, **kwargs): ...
    async def cancel_inventory(self, *args, **kwargs): ...
    async def report_automation_result(self, *args, **kwargs): ...
    async def start_preprocessing(self, *args, **kwargs): ...
    async def preprocessing_status(self, *args, **kwargs): ...
    async def preprocessing_records(self, *args, **kwargs): ...
    async def cancel_preprocessing(self, *args, **kwargs): ...
    async def start_selection(self, *args, **kwargs): ...
    async def selection_status(self, *args, **kwargs): ...
    async def selection_candidates(self, *args, **kwargs): ...
    async def live_selected_records(self, *args, **kwargs): ...
    async def cancel_selection(self, *args, **kwargs): ...
    async def start_transfer(self, *args, **kwargs): ...
    async def transfer_status(self, *args, **kwargs): ...
    async def transfer_manifest(self, *args, **kwargs): ...
    async def cleanup_transfer(self, *args, **kwargs): ...
    async def list_google_accounts(self, *args, **kwargs): ...
    async def get_google_auth_token(self, *args, **kwargs): ...


class AutomationRunner(Protocol):
    async def run(
        self,
        *,
        serial: str,
        session_id: str,
        session_token: str,
        token_expires_at_epoch_ms: int,
        crawl_id: str,
        mode: str,
        not_before_epoch_ms: int,
        target_packages: tuple[str, ...],
        request_id: str | None,
        on_progress: Callable[[str, str], Awaitable[None]] | None = None,
        on_result: Callable[[AutomationResultV1], Awaitable[None]] | None = None,
        on_scope_progress: (
            Callable[[AutomationScopeProgressV1], Awaitable[None]] | None
        ) = None,
    ) -> list[AutomationResultV1]: ...


class SelectionSnapshotRepository(Protocol):
    async def begin_snapshot(self, run: SelectionRunV1) -> None: ...
    async def append_candidates(self, session_id: str, crawl_id: str, candidates) -> None: ...
    async def finish_snapshot(self, run: SelectionRunV1) -> None: ...


class DirectTransferRunner(Protocol):
    async def ingest(
        self,
        context: AcquisitionContext,
        client: InventoryClient,
        selection: SelectionRunV1,
    ) -> AcquisitionResult: ...


class _RecoveringInventoryClient:
    def __init__(
        self,
        delegate: InventoryClient,
        repair: Callable[[], Awaitable[None]],
        *,
        session_id: str,
        request_id: str,
    ) -> None:
        self._delegate = delegate
        self._repair = repair
        self._session_id = session_id
        self._request_id = request_id
        self._repair_lock = asyncio.Lock()
        self._repair_generation = 0

    def __getattr__(self, name: str) -> Any:
        operation = getattr(self._delegate, name)
        if not callable(operation):
            return operation

        async def invoke(*args: Any, **kwargs: Any) -> Any:
            generation = self._repair_generation
            try:
                return await operation(*args, **kwargs)
            except AcquisitionError as exc:
                if exc.category != ErrorCategory.AGENT_UNREACHABLE or not exc.retryable:
                    raise

            async with self._repair_lock:
                if generation == self._repair_generation:
                    logger.warning(
                        "android_agent_connection_recovery_started",
                        extra={
                            "request_id": self._request_id,
                            "session_id": self._session_id,
                        },
                    )
                    await self._repair()
                    self._repair_generation += 1
                    logger.info(
                        "android_agent_connection_recovery_completed",
                        extra={
                            "request_id": self._request_id,
                            "session_id": self._session_id,
                        },
                    )
            return await operation(*args, **kwargs)

        return invoke


class Phase7AndroidAgentRunner:
    def __init__(
        self,
        bootstrap_service: BootstrapService,
        *,
        runtime_registry: RuntimeRegistry,
        client_factory: Callable[[int, str], InventoryClient],
        automation: AutomationRunner | None = None,
        target_packages: tuple[str, ...] = (),
        page_size: int = 100,
        selection_repository: SelectionSnapshotRepository = default_selection_repository,
        transfer: DirectTransferRunner = direct_crawl_transfer,
        connection_repair: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> None:
        if not 1 <= page_size <= 100:
            raise ValueError("inventory page size is invalid")
        self._bootstrap_service = bootstrap_service
        self._runtime_registry = runtime_registry
        self._client_factory = client_factory
        self._automation = automation
        self._target_packages = target_packages
        self._page_size = page_size
        self._selection_repository = selection_repository
        self._transfer = transfer
        self._connection_repair = connection_repair

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        started = time.perf_counter()
        required_access, optional_access = special_access_for_inventory_mode(
            context.mode.value,
            require_accessibility=context.analysis_plan.includes_social,
        )
        await self._bootstrap_service.bootstrap(
            session_id=context.session_id,
            serial=context.device_id,
            request_id=context.request_id,
            on_progress=context.on_progress,
            required_special_access=required_access,
            optional_special_access=optional_access,
        )
        runtime = await self._runtime_registry.get(context.session_id)
        client = self._client_factory(runtime.forward_host_port, runtime.token)
        if self._connection_repair is not None:
            client = _RecoveringInventoryClient(
                client,
                lambda: self._connection_repair(
                    context.device_id,
                    runtime.forward_host_port,
                ),
                session_id=context.session_id,
                request_id=context.request_id,
            )
        policy = build_selection_policy(context.mode)
        if not verify_policy_fingerprint(policy):
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Policy selection SIKSIK tidak valid.",
            )
        configured = (
            await client.bootstrap(
                context.session_id,
                settings.android_agent_api_version,
                selection_policy=policy,
                review_candidates=context.review_candidates,
                request_id=context.request_id,
            )
        ).body
        if configured.session_id != context.session_id or configured.state != "active":
            raise acquisition_error(
                ErrorCategory.AGENT_SESSION_MISMATCH,
                "Policy selection tidak terikat ke sesi Android agent.",
            )
        inventory_started = time.perf_counter()
        inventory = await self._enumerate(context, client, runtime, policy)
        inventory_ms = round((time.perf_counter() - inventory_started) * 1000)
        await context.on_progress(
            SessionStatus.ACQUIRING,
            34.0,
            "Inventaris Android selesai",
            crawl_id=inventory.crawl_id,
            android_inventory_ms=inventory_ms,
        )
        preprocessing_started = time.perf_counter()
        initial_preprocessing = (
            await client.start_preprocessing(
                context.session_id,
                inventory.crawl_id,
                request_id=context.request_id,
            )
        ).body
        selection_started = time.perf_counter()
        preprocessing_task = asyncio.create_task(
            self._preprocess(
                context,
                client,
                inventory,
                initial_run=initial_preprocessing,
            )
        )
        selection_task = asyncio.create_task(
            self._select(context, client, inventory, policy)
        )
        try:
            (preprocessing, preprocessed_records), selection = await asyncio.gather(
                preprocessing_task,
                selection_task,
            )
        except BaseException:
            preprocessing_task.cancel()
            selection_task.cancel()
            await asyncio.gather(
                preprocessing_task,
                selection_task,
                return_exceptions=True,
            )
            raise
        preprocessing_ms = round((time.perf_counter() - preprocessing_started) * 1000)
        selection_ms = round((time.perf_counter() - selection_started) * 1000)
        await context.on_progress(
            SessionStatus.ACQUIRING,
            49.0,
            "Preprocess dan selection Android selesai",
            crawl_id=inventory.crawl_id,
            android_inventory_ms=inventory_ms,
            android_preprocessing_ms=preprocessing_ms,
            android_selection_ms=selection_ms,
        )
        transfer_started = time.perf_counter()
        transfer = await self._transfer.ingest(context, client, selection)
        transfer_ms = round((time.perf_counter() - transfer_started) * 1000)

        # Ingest Gmail if enabled
        if settings.gmail_acquisition_enabled and context.analysis_plan.includes_email:
            from app.acquisition.gmail_oauth import (
                ensure_gmail_oauth,
                session_acquisition_reference,
            )
            from app.acquisition.gmail_service import GmailAcquisitionService
            from app.acquisition.runtime import AgentRuntimeSecrets, agent_runtime_registry

            account_name = getattr(runtime, "google_account", None)
            token = getattr(runtime, "google_token", None)
            if not context.simulated:
                account_name, token = await ensure_gmail_oauth(
                    client=client,
                    session_id=context.session_id,
                    serial=context.device_id,
                    adb=AsyncAdbTransport(settings.adb_path),
                    on_progress=context.on_progress,
                    request_id=context.request_id,
                    existing_account=account_name,
                    existing_token=token,
                )
                runtime = AgentRuntimeSecrets(
                    session_id=runtime.session_id,
                    serial=runtime.serial,
                    token=runtime.token,
                    forward_host_port=runtime.forward_host_port,
                    token_expires_at=runtime.token_expires_at,
                    google_token=token,
                    google_account=account_name,
                )
                await agent_runtime_registry.bind(runtime)

            reference = await session_acquisition_reference(context.session_id)
            gmail_svc = GmailAcquisitionService()
            gmail_count, _ = await gmail_svc.acquire(
                session_id=context.session_id,
                staging=transfer.staging,
                mode=context.mode,
                token=token,
                account_name=account_name,
                simulated=context.simulated,
                on_progress=context.on_progress,
                request_id=context.request_id,
                reference=reference,
            )
            if gmail_count > 0:
                transfer = AcquisitionResult(
                    staging=transfer.staging,
                    item_count=transfer.item_count + gmail_count,
                    duration_ms=transfer.duration_ms,
                    method=f"{transfer.method}+gmail_api",
                    provider=transfer.provider,
                )

        acquisition_ms = round((time.perf_counter() - started) * 1000)
        await context.on_progress(
            SessionStatus.ACQUIRING,
            60.0,
            "Akuisisi Android selesai",
            crawl_id=inventory.crawl_id,
            crawl_state="transfer_committed",
            android_inventory_ms=inventory_ms,
            android_preprocessing_ms=preprocessing_ms,
            android_selection_ms=selection_ms,
            android_transfer_ms=transfer_ms,
            android_acquisition_ms=acquisition_ms,
        )
        inventory_state = "partial" if inventory.state == "partial" else "complete"
        preprocessing_state = (
            "partial" if preprocessing.state == "partial" else preprocessing.state
        )
        logger.info(
            "android_inventory_completed",
            extra={
                "request_id": context.request_id,
                "session_id": context.session_id,
                "crawl_id": inventory.crawl_id,
                "state": inventory.state,
                "discovered_count": inventory.totals.discovered,
                "duplicate_count": inventory.totals.duplicates,
                "preprocessed_count": preprocessed_records,
                "preprocessing_state": preprocessing.state,
                "selection_state": selection.state,
                "selection_revision": selection.revision,
                "selected_count": selection.totals.selected,
                "duration_ms": acquisition_ms,
            },
        )
        return AcquisitionResult(
            staging=transfer.staging,
            item_count=transfer.item_count,
            duration_ms=(time.perf_counter() - started) * 1000,
            method=(
                f"android_agent_inventory_{inventory_state}"
                f"+preprocessing_{preprocessing_state}+selection_confirmed"
                f"+{transfer.method}"
            ),
            provider=ProviderKind.ANDROID_AGENT,
        )

    async def _enumerate(
        self,
        context: AcquisitionContext,
        client: InventoryClient,
        runtime: AgentRuntimeSecrets,
        policy: SelectionPolicyV1,
    ) -> InventoryRunV1:
        configured_targets = set(self._target_packages)
        social_packages = tuple(
            package
            for package in context.analysis_plan.social_packages
            if not configured_targets or package in configured_targets
        )
        enabled_adapters = context.analysis_plan.inventory_adapters()
        response = await client.start_inventory(
            context.session_id,
            context.mode.value,
            document_grant_id=None,
            target_packages=list(social_packages),
            source_adapters=sorted(enabled_adapters),
            request_id=context.request_id,
        )
        run = response.body
        if run.siksik_session_id != context.session_id:
            raise acquisition_error(
                ErrorCategory.AGENT_SESSION_MISMATCH,
                "Crawl Android agent tidak terikat ke sesi SIKSIK.",
            )
        if set(run.source_progress) != INVENTORY_SOURCES:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Daftar sumber inventory Android agent tidak lengkap.",
            )
        try:
            visible = run.source_progress["accessibility_visible_ui"]
            if (
                visible.state in {"pending", "crawling"}
                and self._automation is not None
                and social_packages
            ):
                social_labels = {
                    "com.instagram.android": "Instagram",
                    "com.twitter.android": "X",
                    "com.facebook.katana": "Facebook",
                }
                system_messages = {
                    "build": "Build APK UiAutomator",
                    "install": "Memasang paket UiAutomator",
                    "install_skip": "Paket UiAutomator sudah terbaru",
                }
                instrument_messages = {
                    "target_probe": "Memeriksa target",
                    "preflight_visual_suspend": "Menyiapkan crawl visual",
                    "preflight_accessibility": "Memverifikasi accessibility",
                    "preflight_cover": "Memverifikasi pelindung text-only",
                    "instrument": "Menjalankan instrumentation",
                    "restore_agent": "Memulihkan agent setelah instrumentation",
                    "restore_agent_failed": "Pemulihan agent setelah instrumentation gagal",
                    "restore_accessibility": "Memulihkan accessibility setelah crawl visual",
                }
                scope_labels = {
                    "own_profile": "profil sendiri",
                    "own_posts": "postingan sendiri",
                    "own_tweets": "tweet sendiri",
                    "own_story_archive": "arsip story",
                    "own_comments": "komentar sendiri",
                    "own_replies": "balasan sendiri",
                }
                stage_labels = {
                    "attempt_started": "memulai",
                    "initial_captured": "bukti awal tersimpan",
                    "capture_scrolled": "mengambil halaman lanjutan",
                    "diagnosis": "mendiagnosis kegagalan",
                    "attempt_failed": "percobaan gagal",
                    "recovery_failed": "pemulihan state gagal, mencoba ulang",
                    "state_recovered": "state dipulihkan",
                    "checkpoint_saved": "scope lengkap",
                    "checkpoint_restored": "checkpoint dipulihkan",
                    "checkpoint_failed": "checkpoint gagal",
                }
                reported_targets: set[str] = set()

                async def on_social_progress(target_package: str, phase: str) -> None:
                    if target_package == "__system__":
                        message = system_messages.get(phase, phase)
                        percent = 10.0 if phase == "build" else 11.0
                    else:
                        label = social_labels.get(target_package, target_package)
                        if phase in instrument_messages:
                            message = (
                                f"{instrument_messages[phase]} {label}"
                            )
                            percent = 12.0
                        else:
                            message = f"Crawl sosial {label}: {phase}"
                            percent = 12.0
                    await context.on_progress(
                        SessionStatus.ACQUIRING,
                        percent,
                        message,
                        crawl_id=run.crawl_id,
                        crawl_state="social_automation",
                        crawl_source="accessibility_visible_ui",
                        crawl_target=(
                            None if target_package == "__system__" else target_package
                        ),
                        crawl_stage=phase,
                    )

                async def on_social_scope_progress(
                    progress: AutomationScopeProgressV1,
                ) -> None:
                    if progress.diagnosis:
                        logger.info(
                            "social_scope_diagnosis target=%s scope=%s reason=%s diagnosis=%s",
                            progress.target_package,
                            progress.scope,
                            progress.reason,
                            progress.diagnosis,
                        )
                    app_label = social_labels.get(
                        progress.target_package,
                        progress.target_package,
                    )
                    scope_label = scope_labels.get(progress.scope, progress.scope)
                    stage_label = stage_labels.get(progress.stage, progress.stage)
                    attempt_suffix = (
                        f" · percobaan {progress.attempt}/{SOCIAL_SCOPE_ATTEMPTS}"
                        if progress.attempt > 0
                        else ""
                    )
                    reason_suffix = (
                        f" · {progress.reason}"
                        if progress.reason and progress.stage in {"diagnosis", "attempt_failed"}
                        else ""
                    )
                    await context.on_progress(
                        SessionStatus.ACQUIRING,
                        12.0,
                        f"Crawl {app_label} — {scope_label}: {stage_label}{attempt_suffix}{reason_suffix}",
                        crawl_id=run.crawl_id,
                        crawl_state="social_automation",
                        crawl_source="accessibility_visible_ui",
                        crawl_target=progress.target_package,
                        crawl_scope=progress.scope,
                        crawl_stage=progress.stage,
                        crawl_attempt=progress.attempt,
                        crawl_attempt_state=progress.state,
                        crawl_failure_class=progress.failure_class,
                        crawl_reason=progress.reason,
                        crawl_scroll_count=progress.scroll_count,
                        crawl_screenshot_count=progress.screenshot_count,
                    )

                async def on_social_result(outcome: AutomationResultV1) -> None:
                    await client.report_automation_result(
                        context.session_id,
                        run.crawl_id,
                        outcome,
                        request_id=context.request_id,
                    )
                    reported_targets.add(outcome.target_package)

                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    9.0,
                    "Menyiapkan instrumentation sosial",
                    crawl_id=run.crawl_id,
                    crawl_state="social_automation",
                    crawl_source="accessibility_visible_ui",
                )
                outcomes = await self._automation.run(
                    serial=context.device_id,
                    session_id=context.session_id,
                    session_token=runtime.token,
                    token_expires_at_epoch_ms=self._token_expiry_epoch_ms(runtime),
                    crawl_id=run.crawl_id,
                    mode=context.mode.value,
                    not_before_epoch_ms=build_time_scope(
                        context.mode,
                        reference=datetime.fromisoformat(run.started_at),
                    ).not_before_epoch_ms,
                    target_packages=social_packages,
                    request_id=context.request_id,
                    on_progress=on_social_progress,
                    on_result=on_social_result,
                    on_scope_progress=on_social_scope_progress,
                )
                configured = (
                    await client.bootstrap(
                        context.session_id,
                        settings.android_agent_api_version,
                        selection_policy=policy,
                        review_candidates=context.review_candidates,
                        request_id=context.request_id,
                    )
                ).body
                if configured.session_id != context.session_id or configured.state != "active":
                    raise acquisition_error(
                        ErrorCategory.AGENT_SESSION_MISMATCH,
                        "Android agent tidak pulih setelah automation social.",
                    )
                for outcome in outcomes:
                    if outcome.target_package in reported_targets:
                        continue
                    await client.report_automation_result(
                        context.session_id,
                        run.crawl_id,
                        outcome,
                        request_id=context.request_id,
                    )
            for index, (source, progress) in enumerate(run.source_progress.items()):
                if source not in enabled_adapters:
                    continue
                if progress.state not in {"pending", "crawling"}:
                    continue
                cursor = progress.resume_cursor
                seen_cursors: set[str] = set()
                while True:
                    if cursor is not None:
                        if cursor in seen_cursors:
                            raise acquisition_error(
                                ErrorCategory.AGENT_INVALID_RESPONSE,
                                "Android agent mengulang cursor inventory.",
                            )
                        seen_cursors.add(cursor)
                    page_response = await client.inventory_page(
                        context.session_id,
                        run.crawl_id,
                        source,
                        cursor=cursor,
                        limit=self._page_limit(source),
                        request_id=context.request_id,
                    )
                    page = page_response.body
                    if (
                        page.crawl_id != run.crawl_id
                        or page.siksik_session_id != context.session_id
                        or page.source_adapter != source
                    ):
                        raise acquisition_error(
                            ErrorCategory.AGENT_INVALID_RESPONSE,
                            "Halaman inventory Android agent tidak konsisten.",
                        )
                    await context.on_progress(
                        SessionStatus.ACQUIRING,
                        min(34.0, 10.0 + ((index + 1) / len(run.source_progress)) * 24.0),
                        "Menginventarisasi sumber Android",
                        crawl_id=run.crawl_id,
                        crawl_state="crawling",
                        crawl_source=source,
                        crawl_source_state=page.source_state,
                        crawl_discovered=page.discovered_count,
                        crawl_duplicates=page.duplicate_count,
                    )
                    if page.source_state != "crawling":
                        break
                    if page.next_cursor is None:
                        raise acquisition_error(
                            ErrorCategory.AGENT_INVALID_RESPONSE,
                            "Android agent tidak memberikan cursor lanjutan.",
                        )
                    cursor = page.next_cursor
            final_response = await client.inventory_status(
                context.session_id,
                run.crawl_id,
                request_id=context.request_id,
            )
        except asyncio.CancelledError:
            try:
                await client.cancel_inventory(
                    context.session_id,
                    run.crawl_id,
                    request_id=context.request_id,
                )
            except Exception:
                logger.warning(
                    "android_inventory_cancel_failed",
                    extra={
                        "request_id": context.request_id,
                        "session_id": context.session_id,
                        "crawl_id": run.crawl_id,
                    },
                )
            raise

        final = final_response.body
        if final.state == "cancelled":
            raise asyncio.CancelledError
        if final.state == "failed":
            raise acquisition_error(
                ErrorCategory.AGENT_UNAVAILABLE,
                "Crawl sumber Android gagal.",
                retryable=True,
            )
        if final.state not in {"complete", "partial"}:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Crawl media dan dokumen Android belum mencapai status terminal.",
            )
        if context.mode.value == "full":
            unfinished = {
                source: progress.state
                for source, progress in final.source_progress.items()
                if progress.state in {"pending", "crawling"}
            }
            if unfinished:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Full crawl Android masih memiliki sumber yang belum selesai.",
                )
        await context.on_progress(
            SessionStatus.ACQUIRING,
            35.0,
            "Inventaris sumber Android selesai",
            crawl_id=final.crawl_id,
            crawl_state=final.state,
            crawl_source_progress={
                source: progress.model_dump(mode="json")
                for source, progress in final.source_progress.items()
            },
            crawl_partial_reasons=[item.model_dump(mode="json") for item in final.partial_reasons],
            crawl_resume_cursors=final.resume_cursors,
            crawl_discovered=final.totals.discovered,
            crawl_duplicates=final.totals.duplicates,
        )
        return final

    @staticmethod
    def _token_expiry_epoch_ms(runtime: AgentRuntimeSecrets) -> int:
        try:
            expires_at = datetime.fromisoformat(runtime.token_expires_at)
        except (TypeError, ValueError) as exc:
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Expiry runtime Android agent tidak valid.",
            ) from exc
        if expires_at.tzinfo is None:
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Expiry runtime Android agent tidak valid.",
            )
        value = int(expires_at.timestamp() * 1000)
        if value <= 0:
            raise acquisition_error(
                ErrorCategory.INTERNAL_ERROR,
                "Expiry runtime Android agent tidak valid.",
            )
        return value

    async def _preprocess(
        self,
        context: AcquisitionContext,
        client: InventoryClient,
        inventory: InventoryRunV1,
        *,
        initial_run: PreprocessingRunV1 | None = None,
    ) -> tuple[PreprocessingRunV1, int]:
        run = initial_run
        if run is None:
            run = (
                await client.start_preprocessing(
                    context.session_id,
                    inventory.crawl_id,
                    request_id=context.request_id,
                )
            ).body
        try:
            while run.state == "running":
                if (
                    run.crawl_id != inventory.crawl_id
                    or run.siksik_session_id != context.session_id
                ):
                    raise acquisition_error(
                        ErrorCategory.AGENT_INVALID_RESPONSE,
                        "Status preprocessing Android agent tidak konsisten.",
                    )
                totals = run.totals
                terminal = (
                    totals.completed
                    + totals.skipped
                    + totals.truncated
                    + totals.failed
                    + totals.cancelled
                )
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    min(44.0, 35.0 + (terminal / max(totals.total, 1)) * 9.0),
                    "Memproses data secara lokal di Android",
                    crawl_id=inventory.crawl_id,
                    crawl_state="preprocessing",
                    preprocessing_state=run.state,
                    preprocessing_total=totals.total,
                    preprocessing_completed=totals.completed,
                    preprocessing_skipped=totals.skipped,
                    preprocessing_truncated=totals.truncated,
                    preprocessing_failed=totals.failed,
                    preprocessing_preprocessor_totals={
                        name: value.model_dump(mode="json")
                        for name, value in run.preprocessor_totals.items()
                    },
                )
                await asyncio.sleep(PREPROCESS_POLL_SECONDS)
                run = (
                    await client.preprocessing_status(
                        context.session_id,
                        inventory.crawl_id,
                        request_id=context.request_id,
                    )
                ).body
        except asyncio.CancelledError:
            try:
                await client.cancel_preprocessing(
                    context.session_id,
                    inventory.crawl_id,
                    request_id=context.request_id,
                )
            except Exception:
                logger.warning(
                    "android_preprocessing_cancel_failed",
                    extra={
                        "request_id": context.request_id,
                        "session_id": context.session_id,
                        "crawl_id": inventory.crawl_id,
                    },
                )
            raise
        if run.state == "cancelled":
            raise asyncio.CancelledError
        if run.state == "failed":
            raise acquisition_error(
                ErrorCategory.AGENT_UNAVAILABLE,
                "Preprocessing lokal Android gagal.",
                retryable=True,
            )
        if run.state not in {"complete", "partial"}:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Preprocessing lokal Android belum terminal.",
            )
        terminal_count = (
            run.totals.completed
            + run.totals.skipped
            + run.totals.truncated
            + run.totals.failed
            + run.totals.cancelled
        )
        if terminal_count != run.totals.total:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Jumlah record preprocessing Android agent tidak sesuai.",
            )
        await context.on_progress(
            SessionStatus.ACQUIRING,
            44.0,
            "Preprocessing lokal Android selesai",
            crawl_id=inventory.crawl_id,
            crawl_state="preprocessing_complete",
            preprocessing_state=run.state,
            preprocessing_total=run.totals.total,
            preprocessing_completed=run.totals.completed,
            preprocessing_skipped=run.totals.skipped,
            preprocessing_truncated=run.totals.truncated,
            preprocessing_failed=run.totals.failed,
            preprocessing_preprocessor_totals={
                name: value.model_dump(mode="json")
                for name, value in run.preprocessor_totals.items()
            },
            preprocessing_partial_reasons=run.partial_reasons,
        )
        return run, terminal_count

    async def _select(
        self,
        context: AcquisitionContext,
        client: InventoryClient,
        inventory: InventoryRunV1,
        policy,
    ) -> SelectionRunV1:
        live_cursor: str | None = None
        live_analysis_ms = 0.0
        run = (
            await client.start_selection(
                context.session_id,
                inventory.crawl_id,
                policy.policy_fingerprint,
                context.review_candidates,
                request_id=context.request_id,
            )
        ).body
        try:
            while run.state == "running":
                self._validate_selection_run(context, inventory, policy.policy_fingerprint, run)
                if not context.review_candidates:
                    live_cursor, added_analysis_ms = await self._drain_live_selected(
                        context,
                        client,
                        inventory.crawl_id,
                        live_cursor,
                    )
                    live_analysis_ms += added_analysis_ms
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    min(
                        49.0,
                        44.0
                        + (run.totals.evaluated / max(run.totals.total, 1)) * 5.0,
                    ),
                    "Menyeleksi candidate secara lokal di Android",
                    crawl_id=inventory.crawl_id,
                    crawl_state="selecting",
                    selection_state=run.state,
                    selection_revision=run.revision,
                    selection_policy_version=run.policy_version,
                    selection_policy_fingerprint=run.policy_fingerprint,
                    selection_evaluated=run.totals.evaluated,
                    selection_candidates=run.totals.candidates,
                    selection_selected=run.totals.selected,
                    selection_below_threshold=run.totals.below_threshold,
                    live_analysis_ms=round(live_analysis_ms, 1),
                )
                await asyncio.sleep(SELECTION_POLL_SECONDS)
                run = (
                    await client.selection_status(
                        context.session_id,
                        inventory.crawl_id,
                        request_id=context.request_id,
                    )
                ).body
            self._validate_selection_run(context, inventory, policy.policy_fingerprint, run)
            if not context.review_candidates:
                live_cursor, added_analysis_ms = await self._drain_live_selected(
                    context,
                    client,
                    inventory.crawl_id,
                    live_cursor,
                )
                live_analysis_ms += added_analysis_ms
            if run.state == "cancelled":
                raise asyncio.CancelledError
            if run.state == "failed":
                raise acquisition_error(
                    ErrorCategory.AGENT_UNAVAILABLE,
                    "Selection lokal Android gagal.",
                    retryable=True,
                )
            if run.state not in {"awaiting_review", "confirmed"}:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Selection lokal Android belum mencapai revision beku.",
                )
            await self._mirror_selection_snapshot(context, client, run)
            await context.on_progress(
                (
                    SessionStatus.AWAITING_REVIEW
                    if run.state == "awaiting_review"
                    else SessionStatus.SELECTING
                ),
                49.0,
                (
                    "Menunggu review candidate operator"
                    if run.state == "awaiting_review"
                    else "Selection candidate selesai"
                ),
                crawl_id=inventory.crawl_id,
                crawl_state=run.state,
                selection_state=run.state,
                selection_revision=run.revision,
                selection_fingerprint=run.selection_fingerprint,
                selection_policy_version=run.policy_version,
                selection_policy_fingerprint=run.policy_fingerprint,
                selection_evaluated=run.totals.evaluated,
                selection_candidates=run.totals.candidates,
                selection_selected=run.totals.selected,
                selection_below_threshold=run.totals.below_threshold,
                selection_selected_bytes=run.totals.selected_bytes,
                live_analysis_ms=round(live_analysis_ms, 1),
            )
            while run.state == "awaiting_review":
                await asyncio.sleep(SELECTION_POLL_SECONDS)
                run = (
                    await client.selection_status(
                        context.session_id,
                        inventory.crawl_id,
                        request_id=context.request_id,
                    )
                ).body
                self._validate_selection_run(
                    context,
                    inventory,
                    policy.policy_fingerprint,
                    run,
                )
            if run.state != "confirmed" or run.selection_fingerprint is None:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Selection Android tidak dikonfirmasi.",
                )
            await self._selection_repository.finish_snapshot(run)
            await context.on_progress(
                SessionStatus.SELECTING,
                50.0,
                "Selection candidate dikonfirmasi",
                crawl_id=inventory.crawl_id,
                crawl_state="selection_confirmed",
                selection_state=run.state,
                selection_revision=run.revision,
                selection_fingerprint=run.selection_fingerprint,
                selection_selected=run.totals.selected,
                selection_selected_bytes=run.totals.selected_bytes,
                live_analysis_ms=round(live_analysis_ms, 1),
            )
            return run
        except asyncio.CancelledError:
            try:
                cancelled = await client.cancel_selection(
                    context.session_id,
                    inventory.crawl_id,
                    request_id=context.request_id,
                )
                await self._selection_repository.finish_snapshot(cancelled.body)
            except Exception:
                logger.warning(
                    "android_selection_cancel_failed",
                    extra={
                        "request_id": context.request_id,
                        "session_id": context.session_id,
                        "crawl_id": inventory.crawl_id,
                    },
                )
            raise

    async def _drain_live_selected(
        self,
        context: AcquisitionContext,
        client: InventoryClient,
        crawl_id: str,
        cursor: str | None,
    ) -> tuple[str | None, float]:
        current = cursor
        analysis_ms = 0.0
        pending_records = []

        async def flush() -> None:
            nonlocal analysis_ms
            if not pending_records:
                return
            analysis_started = time.perf_counter()
            await live_selected_ingestor.ingest(
                session_id=context.session_id,
                crawl_id=crawl_id,
                records=list(pending_records),
                mode=context.mode,
                on_progress=context.on_progress,
            )
            analysis_ms += (time.perf_counter() - analysis_started) * 1000
            pending_records.clear()

        while True:
            page = (
                await client.live_selected_records(
                    context.session_id,
                    crawl_id,
                    cursor=current,
                    limit=LIVE_SELECTION_PAGE_LIMIT,
                    request_id=context.request_id,
                )
            ).body
            if (
                page.crawl_id != crawl_id
                or page.siksik_session_id != context.session_id
                or page.review_candidates
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Halaman live selection Android tidak konsisten.",
                )
            if not page.records:
                await flush()
                return current, analysis_ms
            next_cursor = page.next_cursor
            if next_cursor is None or next_cursor == current:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Cursor live selection Android tidak bergerak.",
                )
            pending_records.extend(page.records)
            current = next_cursor
            if len(pending_records) >= LIVE_ANALYSIS_BATCH_SIZE:
                await flush()
            if len(page.records) < LIVE_SELECTION_PAGE_LIMIT:
                await flush()
                return current, analysis_ms

    async def _mirror_selection_snapshot(
        self,
        context: AcquisitionContext,
        client: InventoryClient,
        run: SelectionRunV1,
    ) -> None:
        await self._selection_repository.begin_snapshot(run)
        cursor: str | None = None
        seen_cursors: set[str] = set()
        mirrored = 0
        while True:
            page = (
                await client.selection_candidates(
                    context.session_id,
                    run.crawl_id,
                    cursor=cursor,
                    limit=SELECTION_CANDIDATE_PAGE_LIMIT,
                    request_id=context.request_id,
                )
            ).body
            if (
                page.crawl_id != run.crawl_id
                or page.siksik_session_id != context.session_id
                or page.revision != run.revision
                or page.selection_fingerprint != run.selection_fingerprint
            ):
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Snapshot candidate Android agent tidak konsisten.",
                )
            await self._selection_repository.append_candidates(
                context.session_id,
                run.crawl_id,
                page.records,
            )
            mirrored += len(page.records)
            if page.next_cursor is None:
                break
            if page.next_cursor in seen_cursors:
                raise acquisition_error(
                    ErrorCategory.AGENT_INVALID_RESPONSE,
                    "Android agent mengulang cursor candidate.",
                )
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        if mirrored != run.totals.evaluated:
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Jumlah candidate selection Android tidak sesuai ledger.",
            )
        await self._selection_repository.finish_snapshot(run)

    @staticmethod
    def _validate_selection_run(
        context: AcquisitionContext,
        inventory: InventoryRunV1,
        policy_fingerprint: str,
        run: SelectionRunV1,
    ) -> None:
        if (
            run.crawl_id != inventory.crawl_id
            or run.siksik_session_id != context.session_id
            or run.policy_fingerprint != policy_fingerprint
            or run.review_candidates != context.review_candidates
        ):
            raise acquisition_error(
                ErrorCategory.AGENT_INVALID_RESPONSE,
                "Status selection Android agent tidak konsisten.",
            )

    def _page_limit(self, source: str) -> int:
        return min(self._page_size, INVENTORY_PAGE_LIMITS[source])


Phase5AndroidAgentRunner = Phase7AndroidAgentRunner
Phase4AndroidAgentRunner = Phase7AndroidAgentRunner

PREPROCESS_POLL_SECONDS = 0.25
SELECTION_POLL_SECONDS = 0.25
