"""Operator iOS preflight: USB Trust, Developer Mode, WDA install, Trust profil."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.ios_setup_host import LiveIosSetupHost
from app.acquisition.ios_setup_log import redact_text, setup_ios_log_path, write_setup_ios_log
from app.acquisition.ios_social import _ios_device_ref, validate_ios_udid
from app.models.schemas import DeviceType, IosSetupState, IosSetupStatus, StartSessionRequest

logger = logging.getLogger("siksik.acquisition.ios_setup")

_USB_BLOCKING = frozenset(
    {IosSetupState.USB_UNPAIRED, IosSetupState.AWAITING_USB_TRUST}
)


def _status(
    *,
    state: IosSetupState,
    message: str,
    paired: bool = False,
    developer_mode: bool | None = None,
    wda_installed: bool = False,
    wda_trusted: bool | None = None,
    apple_id_hint: str | None = None,
) -> IosSetupStatus:
    return IosSetupStatus(
        state=state,
        message=message,
        paired=paired,
        developer_mode=developer_mode,
        wda_installed=wda_installed,
        wda_trusted=wda_trusted,
        apple_id_hint=apple_id_hint,
        ready=state == IosSetupState.READY,
        code_required=state == IosSetupState.AWAITING_APPLE_ID_CODE,
    )


@dataclass
class _SetupJob:
    udid: str
    state: IosSetupState = IosSetupState.INSTALLING_WDA
    message: str = "Menyiapkan iPhone…"
    paired: bool = False
    developer_mode: bool | None = None
    wda_installed: bool = False
    wda_trusted: bool | None = None
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task[None] | None = None
    code_submitted: bool = False
    drain_task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class IosSetupService:
    def __init__(self, host: Any | None = None) -> None:
        self._host = host if host is not None else LiveIosSetupHost()
        self._lock = asyncio.Lock()
        self._job: _SetupJob | None = None
        self._trusted: dict[str, bool] = {}
        self.pair_polls = 12
        self.devmode_polls = 8
        self.poll_sleep_s = 2.0

    async def shutdown(self) -> None:
        async with self._lock:
            job = self._job
            self._job = None
        if job is not None:
            await self._stop_job(job)

    async def status(self, device_id: str) -> IosSetupStatus:
        udid = validate_ios_udid(device_id)
        async with self._lock:
            job = self._job
            if job is not None and job.udid == udid:
                return self._status_from_job(job)
        return await self._probe(udid)

    async def start(self, device_id: str) -> IosSetupStatus:
        udid = validate_ios_udid(device_id)
        from app.services.sessions import sessions

        if await sessions.has_in_flight():
            raise RuntimeError(
                "Sesi akuisisi masih berjalan. Selesaikan atau batalkan dulu sebelum menyiapkan iPhone."
            )
        async with self._lock:
            if self._job is not None and self._job.udid != udid:
                raise RuntimeError(
                    "Penyiapan iPhone lain masih berjalan. Batalkan dulu, atau cabut HP sebelumnya."
                )
            if self._job is not None and self._job.udid == udid:
                if self._job.task is not None and not self._job.task.done():
                    return self._status_from_job(self._job)
                await self._stop_job(self._job)
                self._job = None
            job = _SetupJob(
                udid=udid,
                state=IosSetupState.AWAITING_USB_TRUST,
                message="Meminta Trust USB di iPhone…",
            )
            job.task = asyncio.create_task(self._run_start(job))
            self._job = job
            logger.info(
                "ios_setup_started",
                extra={"device_ref": _ios_device_ref(udid)},
            )
            write_setup_ios_log(
                "INFO",
                "setup_started",
                detail=f"device={_ios_device_ref(udid)} log={setup_ios_log_path()}",
                udid=udid,
            )
            return self._status_from_job(job)

    async def submit_code(self, device_id: str, code: str) -> IosSetupStatus:
        udid = validate_ios_udid(device_id)
        async with self._lock:
            job = self._job
        if job is None or job.udid != udid:
            raise RuntimeError("Tidak ada pemasangan WDA yang menunggu kode.")
        if job.state != IosSetupState.AWAITING_APPLE_ID_CODE:
            raise RuntimeError("Kode 6 digit tidak diperlukan pada langkah ini.")
        process = job.process
        if process is None or process.stdin is None or process.returncode is not None:
            await self._fail(job, "Proses pemasangan WDA sudah selesai atau terputus.")
            raise RuntimeError("Proses pemasangan WDA sudah selesai. Jalankan Siapkan iPhone lagi.")
        try:
            process.stdin.write(f"{code}\n".encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            await self._fail(job, "Tidak bisa mengirim kode verifikasi ke AltServer.")
            raise RuntimeError("Gagal mengirim kode. Jalankan Siapkan iPhone lagi.") from exc
        job.code_submitted = True
        job.state = IosSetupState.INSTALLING_WDA
        job.message = "Kode diterima. Menunggu WebDriverAgent terpasang…"
        logger.info(
            "ios_setup_code_submitted",
            extra={"device_ref": _ios_device_ref(udid), "code_length": 6},
        )
        write_setup_ios_log(
            "INFO",
            "apple_id_code_submitted",
            detail="code_length=6",
            udid=udid,
        )
        return self._status_from_job(job)

    async def ack_trust(self, device_id: str) -> IosSetupStatus:
        udid = validate_ios_udid(device_id)
        bundle = await self._host.list_wda_bundle(udid, use_tunnel=True)
        if not bundle:
            return _status(
                state=IosSetupState.NEEDS_WDA,
                message="WebDriverAgent belum terpasang. Jalankan Siapkan iPhone.",
                paired=True,
                apple_id_hint=self._host.apple_id_hint(),
            )
        launch = await self._host.launch_bundle(udid, bundle)
        write_setup_ios_log(
            "INFO",
            "ack_trust",
            detail=f"launch={launch} bundle={bundle}",
            udid=udid,
        )
        async with self._lock:
            job = self._job if self._job is not None and self._job.udid == udid else None
        if launch == "untrusted":
            self._trusted.pop(udid, None)
            message = (
                "Profil developer belum di-Trust. Settings → General → "
                "VPN & Device Management → Trust Apple ID yang dipakai sign."
            )
            if job is not None:
                job.wda_installed = True
                job.wda_trusted = False
                job.state = IosSetupState.AWAITING_DEVELOPER_TRUST
                job.message = message
                return self._status_from_job(job)
            return _status(
                state=IosSetupState.AWAITING_DEVELOPER_TRUST,
                message=message,
                paired=True,
                wda_installed=True,
                wda_trusted=False,
                apple_id_hint=self._host.apple_id_hint(),
            )
        if launch != "ok":
            http_ok = await self._host.wda_http_ready() and self._host.stack_udid_matches(udid)
            if not http_ok:
                message = "WebDriverAgent terpasang tetapi belum bisa diluncurkan. Cek Trust profil, lalu periksa lagi."
                if job is not None:
                    job.wda_installed = True
                    job.state = IosSetupState.AWAITING_DEVELOPER_TRUST
                    job.message = message
                    return self._status_from_job(job)
                return _status(
                    state=IosSetupState.AWAITING_DEVELOPER_TRUST,
                    message=message,
                    paired=True,
                    wda_installed=True,
                    apple_id_hint=self._host.apple_id_hint(),
                )
        self._trusted[udid] = True
        if job is not None:
            job.wda_installed = True
            job.wda_trusted = True
            job.paired = True
            job.state = IosSetupState.READY
            job.message = "iPhone siap. Jalankan akuisisi."
            return self._status_from_job(job)
        return _status(
            state=IosSetupState.READY,
            message="iPhone siap. Jalankan akuisisi.",
            paired=True,
            wda_installed=True,
            wda_trusted=True,
            apple_id_hint=self._host.apple_id_hint(),
        )

    async def cancel(self, device_id: str) -> IosSetupStatus:
        udid = validate_ios_udid(device_id)
        async with self._lock:
            job = self._job
            if job is None or job.udid != udid:
                return await self._probe(udid)
            self._job = None
        await self._stop_job(job)
        logger.info("ios_setup_cancelled", extra={"device_ref": _ios_device_ref(udid)})
        write_setup_ios_log("INFO", "setup_cancelled", detail=f"device={_ios_device_ref(udid)}", udid=udid)
        return await self._probe(udid)

    async def assert_session_allowed(self, req: StartSessionRequest) -> None:
        if req.device_type != DeviceType.IOS:
            return
        device_id = req.device_id or ""
        if req.force_simulated or device_id.startswith("sim-"):
            return
        if not device_id:
            raise RuntimeError("Pilih iPhone USB sebelum memulai akuisisi.")
        plan = req.analysis_plan()
        status = await self.status(device_id)
        if plan.includes_social:
            if not status.ready:
                raise RuntimeError(
                    status.message
                    or "Siapkan iPhone dulu (USB Trust, Developer Mode, WebDriverAgent) sebelum akuisisi sosmed."
                )
            return
        if status.state in _USB_BLOCKING:
            raise RuntimeError(
                "iPhone belum Trust komputer ini. Unlock HP, ketuk Trust This Computer, lalu Siapkan iPhone."
            )

    async def probe_wda_installed(self, udid: str) -> bool:
        bundle = await self._host.list_wda_bundle(udid)
        return bool(bundle)

    def _status_from_job(self, job: _SetupJob) -> IosSetupStatus:
        return _status(
            state=job.state,
            message=job.message,
            paired=job.paired,
            developer_mode=job.developer_mode,
            wda_installed=job.wda_installed,
            wda_trusted=job.wda_trusted,
            apple_id_hint=self._host.apple_id_hint(),
        )

    async def _probe(self, udid: str) -> IosSetupStatus:
        hint = self._host.apple_id_hint()
        paired = await self._host.pair_validate(udid)
        if not paired:
            return _status(
                state=IosSetupState.USB_UNPAIRED,
                message="Colok USB, unlock iPhone, lalu ketuk Trust This Computer.",
                apple_id_hint=hint,
            )
        http_ok = False
        try:
            http_ok = await self._host.wda_http_ready() and self._host.stack_udid_matches(udid)
        except AcquisitionError:
            http_ok = False
        bundle = await self._host.list_wda_bundle(udid)
        installed = bool(bundle)
        if http_ok and installed:
            self._trusted[udid] = True
            return _status(
                state=IosSetupState.READY,
                message="iPhone siap. Jalankan akuisisi.",
                paired=True,
                wda_installed=True,
                wda_trusted=True,
                apple_id_hint=hint,
            )
        if installed and self._trusted.get(udid):
            return _status(
                state=IosSetupState.READY,
                message="iPhone siap. Jalankan akuisisi.",
                paired=True,
                wda_installed=True,
                wda_trusted=True,
                apple_id_hint=hint,
            )
        if installed:
            return _status(
                state=IosSetupState.AWAITING_DEVELOPER_TRUST,
                message=(
                    "WebDriverAgent sudah terpasang. Settings → General → "
                    "VPN & Device Management → Trust, lalu ketuk Sudah di-Trust."
                ),
                paired=True,
                wda_installed=True,
                wda_trusted=False,
                apple_id_hint=hint,
            )
        developer_mode: bool | None = None
        try:
            developer_mode = await self._host.developer_mode_enabled(udid)
        except AcquisitionError:
            developer_mode = None
        if developer_mode is False:
            return _status(
                state=IosSetupState.DEVELOPER_MODE_OFF,
                message=(
                    "Developer Mode masih OFF. Settings → Privacy & Security → "
                    "Developer Mode → ON, restart jika diminta, lalu Siapkan iPhone lagi."
                ),
                paired=True,
                developer_mode=False,
                apple_id_hint=hint,
            )
        return _status(
            state=IosSetupState.NEEDS_WDA,
            message="WebDriverAgent belum terpasang. Jalankan Siapkan iPhone.",
            paired=True,
            developer_mode=developer_mode,
            apple_id_hint=hint,
        )

    async def _run_start(self, job: _SetupJob) -> None:
        udid = job.udid
        try:
            await self._ensure_paired(job)
            if not job.paired or job.state == IosSetupState.FAILED:
                return
            try:
                await self._host.ensure_tunnel(udid)
            except AcquisitionError:
                logger.info(
                    "ios_setup_tunnel_skipped",
                    extra={"device_ref": _ios_device_ref(udid)},
                )
            await self._ensure_developer_mode(job)
            if job.state in {IosSetupState.DEVELOPER_MODE_OFF, IosSetupState.FAILED}:
                return
            job.state = IosSetupState.INSTALLING_WDA
            job.message = "Memasang WebDriverAgent ke iPhone…"
            write_setup_ios_log(
                "INFO",
                "installing_wda",
                detail="memasang WebDriverAgentRunner.ipa",
                udid=udid,
            )
            bundle = await self._host.list_wda_bundle(udid, use_tunnel=True)
            if bundle:
                job.wda_installed = True
                job.state = IosSetupState.AWAITING_DEVELOPER_TRUST
                job.message = (
                    "WebDriverAgent terpasang. Settings → General → VPN & Device Management → "
                    "Trust Apple ID, lalu ketuk Sudah di-Trust."
                )
                return
            ipa = self._host.resolve_ipa()
            if ipa is not None:
                installed = await self._host.install_ipa(udid, ipa)
                if installed:
                    job.wda_installed = True
                    job.state = IosSetupState.AWAITING_DEVELOPER_TRUST
                    job.message = (
                        "WebDriverAgent terpasang. Settings → General → VPN & Device Management → "
                        "Trust Apple ID, lalu ketuk Sudah di-Trust."
                    )
                    return
            await self._run_altserver(job, ipa)
        except asyncio.CancelledError:
            raise
        except AcquisitionError as exc:
            await self._fail(job, exc.public_message)
        except (OSError, RuntimeError, ValueError) as exc:
            await self._fail(job, "Penyiapan iPhone gagal. Cek USB dan coba lagi.")
            logger.info(
                "ios_setup_failed",
                extra={
                    "device_ref": _ios_device_ref(udid),
                    "error_category": ErrorCategory.INTERNAL_ERROR.value,
                    "error_type": type(exc).__name__,
                },
            )

    async def _ensure_paired(self, job: _SetupJob) -> None:
        udid = job.udid
        job.state = IosSetupState.AWAITING_USB_TRUST
        job.message = "Unlock iPhone dan ketuk Trust This Computer jika muncul."
        if await self._host.pair_validate(udid):
            job.paired = True
            write_setup_ios_log("INFO", "usb_pair", detail="already_trusted", udid=udid)
            return
        await self._host.pair_request(udid)
        for _ in range(self.pair_polls):
            if await self._host.pair_validate(udid):
                job.paired = True
                write_setup_ios_log("INFO", "usb_pair", detail="trusted", udid=udid)
                return
            await asyncio.sleep(self.poll_sleep_s)
        job.paired = False
        job.state = IosSetupState.USB_UNPAIRED
        job.message = "iPhone belum Trust komputer ini. Unlock HP, ketuk Trust, lalu Siapkan iPhone lagi."

    async def _ensure_developer_mode(self, job: _SetupJob) -> None:
        udid = job.udid
        enabled = await self._host.developer_mode_enabled(udid)
        if enabled is True:
            job.developer_mode = True
            write_setup_ios_log("INFO", "developer_mode", detail="on", udid=udid)
            return
        try:
            await self._host.reveal_developer_mode(udid)
        except AcquisitionError:
            pass
        for _ in range(self.devmode_polls):
            enabled = await self._host.developer_mode_enabled(udid)
            if enabled is True:
                job.developer_mode = True
                write_setup_ios_log("INFO", "developer_mode", detail="on", udid=udid)
                return
            job.state = IosSetupState.DEVELOPER_MODE_OFF
            job.message = (
                "Nyalakan Developer Mode di iPhone: Settings → Privacy & Security → "
                "Developer Mode → ON."
            )
            await asyncio.sleep(self.poll_sleep_s)
        job.developer_mode = False
        job.state = IosSetupState.DEVELOPER_MODE_OFF
        job.message = (
            "Developer Mode masih OFF. Selesaikan di iPhone, lalu Siapkan iPhone lagi."
        )

    async def _run_altserver(self, job: _SetupJob, ipa: Any) -> None:
        creds = self._host.apple_credentials()
        if creds is None:
            await self._fail(
                job,
                "APPLE_ID / APPLE_ID_PASSWORD belum diset di ios-media-puller/.env.",
            )
            return
        if ipa is None:
            await self._fail(job, "Berkas WebDriverAgentRunner.ipa tidak ditemukan.")
            return
        apple_id, password = creds
        job.state = IosSetupState.AWAITING_APPLE_ID_CODE
        job.message = (
            "Lihat kode 6 digit di layar iPhone, lalu masukkan di SATRIA. "
            "Setelah terpasang: Settings → General → VPN & Device Management → Trust."
        )
        process = await self._host.start_altserver(job.udid, ipa, apple_id, password)
        job.process = process
        job.drain_task = asyncio.create_task(self._drain_altserver(process, job.udid))
        returncode = await process.wait()
        job.process = None
        if returncode == 0:
            job.wda_installed = True
            job.state = IosSetupState.AWAITING_DEVELOPER_TRUST
            job.message = (
                "WebDriverAgent terpasang. Settings → General → VPN & Device Management → "
                "Trust Apple ID, lalu ketuk Sudah di-Trust."
            )
            return
        if job.code_submitted:
            await self._fail(
                job,
                "Pemasangan WebDriverAgent gagal setelah kode dikirim. Coba Siapkan iPhone lagi.",
            )
            return
        await self._fail(
            job,
            "Pemasangan WebDriverAgent gagal. Pastikan kode 6 digit dimasukkan saat diminta.",
        )

    async def _drain_altserver(self, process: asyncio.subprocess.Process, udid: str) -> None:
        stream = process.stdout
        if stream is None:
            return
        pending = b""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                if pending:
                    line = redact_text(pending.decode("utf-8", errors="replace"), udid=udid)
                    if line.strip():
                        write_setup_ios_log("CMD", "altserver", detail=line.strip(), udid=udid)
                return
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                line = redact_text(raw.decode("utf-8", errors="replace"), udid=udid).strip()
                if line:
                    write_setup_ios_log("CMD", "altserver", detail=line, udid=udid)

    async def _fail(self, job: _SetupJob, message: str) -> None:
        job.state = IosSetupState.FAILED
        job.message = f"{message} Detail: {setup_ios_log_path()}"
        write_setup_ios_log("ERROR", "setup_failed", detail=message, udid=job.udid)
        logger.info(
            "ios_setup_failed",
            extra={
                "device_ref": _ios_device_ref(job.udid),
                "error_category": ErrorCategory.AGENT_INSTALL_FAILED.value,
            },
        )

    async def _stop_job(self, job: _SetupJob) -> None:
        task = job.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, AcquisitionError, OSError):
                pass
        process = job.process
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await process.wait()
            except (ProcessLookupError, OSError):
                pass
        if job.drain_task is not None and not job.drain_task.done():
            job.drain_task.cancel()
            try:
                await job.drain_task
            except (asyncio.CancelledError, OSError):
                pass


ios_setup = IosSetupService()
