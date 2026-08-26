from __future__ import annotations

import shutil
import time

from app.acquisition.adb import validate_serial
from app.acquisition.contracts import (
    AcquisitionContext,
    AcquisitionResult,
    ProviderKind,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.models.schemas import SessionStatus


class SimulatorProvider:
    kind = ProviderKind.SIMULATOR

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        if context.archive is not None:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Provider simulator tidak menerima arsip.",
            )
        from app.services import acquisition as legacy

        staging, count, duration, method = await legacy.acquire_simulated(
            context.session_id,
            context.device_id,
            context.mode,
            context.scenario,
            context.file_count,
            context.on_progress,
        )
        return AcquisitionResult(staging, count, duration, method, self.kind)


class AndroidLegacyProvider:
    kind = ProviderKind.ANDROID_LEGACY

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        if context.archive is not None:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Provider Android live tidak menerima arsip.",
            )
        validate_serial(context.device_id)
        from app.services import acquisition as legacy

        staging, count, duration, method = await legacy.acquire_android_adb(
            context.session_id,
            context.device_id,
            context.mode,
            context.on_progress,
        )
        return AcquisitionResult(staging, count, duration, method, self.kind)


class IOSProvider:
    kind = ProviderKind.IOS

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        """iOS: AFC media/docs + selective SMS/contacts + WDA social IG/X.

        Mirrors Android's multi-source intent without importing Android agent code.
        FULL uses uncapped counts (0); QUICK stays capped.
        """
        if context.archive is not None:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Provider iOS tidak menerima arsip.",
            )
        from app.acquisition.ios_afc import (
            acquire_ios_afc_docs,
            acquire_ios_afc_media,
            ensure_ios_staging,
        )
        from app.core.config import settings
        from app.services import acquisition as legacy

        t0 = time.perf_counter()
        staging = settings.staging_dir / context.session_id
        count = 0
        methods: list[str] = []
        errors: list[str] = []

        if settings.ios_libimobiledevice_backup_enabled:
            staging, backup_count, _dur, backup_method = await legacy.acquire_ios_libimobiledevice(
                context.session_id,
                context.device_id,
                context.mode,
                context.on_progress,
            )
            count += backup_count
            if backup_count > 0:
                methods.append(backup_method)
        else:
            if staging.exists():
                shutil.rmtree(staging)
            ensure_ios_staging(staging)

        # Social first: WDA runs on a clean USB before backup2/AFC hold lockdown.
        if settings.ios_social_ui_enabled and context.analysis_plan.includes_social:
            from app.acquisition.ios_social import acquire_ios_social_ui

            try:
                social_count = await acquire_ios_social_ui(
                    context.session_id,
                    context.device_id,
                    staging,
                    context.mode,
                    context.on_progress,
                    target_packages=context.analysis_plan.social_packages,
                )
                count += social_count
                if social_count > 0:
                    methods.append("ios_wda_social")
            except AcquisitionError as exc:
                errors.append(exc.public_message)
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    24,
                    f"iOS social UI dilewati: {exc.public_message}",
                    acquisition_method="ios_wda_social",
                )

        # AFC gallery/video — primary media path when full backup is off/empty.
        if settings.ios_afc_media_enabled and context.analysis_plan.includes_gallery:
            try:
                media_count = await acquire_ios_afc_media(
                    context.session_id,
                    context.device_id,
                    staging,
                    context.mode,
                    context.on_progress,
                )
                count += media_count
                if media_count > 0:
                    methods.append("ios_afc_media")
            except AcquisitionError as exc:
                errors.append(exc.public_message)
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    28,
                    f"iOS AFC media dilewati: {exc.public_message}",
                    acquisition_method="ios_afc_media",
                )

        if settings.ios_afc_docs_enabled and context.analysis_plan.includes_documents:
            try:
                docs_count = await acquire_ios_afc_docs(
                    context.session_id,
                    context.device_id,
                    staging,
                    context.mode,
                    context.on_progress,
                )
                count += docs_count
                if docs_count > 0:
                    methods.append("ios_afc_docs")
            except AcquisitionError as exc:
                errors.append(exc.public_message)
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    36,
                    f"iOS AFC dokumen dilewati: {exc.public_message}",
                    acquisition_method="ios_afc_docs",
                )

        if settings.ios_sms_contacts_enabled and (
            context.analysis_plan.includes_sms or context.analysis_plan.includes_contacts
        ):
            from app.acquisition.ios_backup_comms import acquire_ios_backup_comms

            try:
                comms_count = await acquire_ios_backup_comms(
                    context.session_id,
                    context.device_id,
                    staging,
                    context.mode,
                    context.on_progress,
                )
                count += comms_count
                if comms_count > 0:
                    methods.append("ios_backup_comms")
            except AcquisitionError as exc:
                errors.append(exc.public_message)
                await context.on_progress(
                    SessionStatus.ACQUIRING,
                    42,
                    f"iOS Messages/Contacts dilewati: {exc.public_message}",
                    acquisition_method="ios_backup_comms",
                )

        duration = (time.perf_counter() - t0) * 1000
        method = "+".join(methods) if methods else "ios_empty"

        if count == 0:
            detail = "; ".join(errors) if errors else "tidak ada media/dokumen/pesan/social"
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                f"Akuisisi iOS kosong: {detail}.",
                retryable=True,
            )

        return AcquisitionResult(staging, count, duration, method, self.kind)


class ZipUploadProvider:
    kind = ProviderKind.ZIP_UPLOAD

    async def acquire(self, context: AcquisitionContext) -> AcquisitionResult:
        if context.archive is None:
            raise acquisition_error(
                ErrorCategory.VALIDATION_ERROR,
                "Provider ZIP membutuhkan payload arsip.",
            )
        from app.services import acquisition as legacy

        staging, count, duration, method = await legacy.acquire_from_zip(
            context.session_id,
            context.archive.content,
            on_progress=context.on_progress,
            original_name=context.archive.original_name,
        )
        return AcquisitionResult(staging, count, duration, method, self.kind)
