from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.agent_artifact import AgentArtifactConfig, AgentArtifactService
from app.acquisition.agent_client import AgentClient, AgentClientConfig
from app.acquisition.apk_metadata import ApkMetadataConfig, ApkMetadataInspector
from app.acquisition.bootstrap import AgentBootstrapConfig, AndroidAgentBootstrapService
from app.acquisition.runtime import AgentRuntimeRegistry, AgentRuntimeRepository
from app.core.db import Database, utcnow


async def insert_session(database: Database, session_id: str, serial: str) -> None:
    now = utcnow()
    await database.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            serial,
            "android",
            "Validasi bootstrap Android agent",
            "quick",
            "lulus",
            "pending",
            json.dumps({"phase": "pending", "percent": 0, "message": "Menunggu"}),
            json.dumps({}),
            None,
            None,
            now,
            now,
        ),
    )


async def validate(serial: str) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[2]
    agent_project = project_root / "android-agent"
    android_home = Path("/opt/homebrew/share/android-commandlinetools")
    java_home = Path("/opt/homebrew/opt/openjdk@17")
    adb = AsyncAdbTransport(
        str(android_home / "platform-tools" / "adb"),
        timeout_seconds=30,
    )
    artifacts = AgentArtifactService(
        AgentArtifactConfig(
            project_path=agent_project,
            apk_path=agent_project / "app/build/outputs/apk/debug/app-debug.apk",
            build_timeout_seconds=600,
            java_home=java_home,
            android_home=android_home,
        )
    )
    inspector = ApkMetadataInspector(
        ApkMetadataConfig(
            android_home=android_home,
            java_home=java_home,
            timeout_seconds=60,
        )
    )
    client_config = AgentClientConfig(timeout_seconds=10, max_attempts=3)

    with tempfile.TemporaryDirectory(prefix="siksik-phase3-") as raw_temp:
        temporary = Path(raw_temp)
        database = Database(temporary / "validation.db")
        await database.connect()
        repository = AgentRuntimeRepository(database)
        service = AndroidAgentBootstrapService(
            AgentBootstrapConfig(
                package_name="com.siksik.agent",
                component="com.siksik.agent/.session.BootstrapActivity",
                api_version="1.0",
                device_port=38471,
                minimum_api=26,
                token_ttl_seconds=600,
                install_timeout_seconds=180,
                minimum_device_storage_bytes=128 * 1024 * 1024,
                special_access_timeout_seconds=60,
                special_access_poll_seconds=1,
                required_special_access=(),
                accessibility_component="com.siksik.agent/.access.SiksikAccessibilityService",
                notification_component="com.siksik.agent/.access.SiksikNotificationListenerService",
                inspection_root=temporary / "inspection",
                # Script memverifikasi skip-install saat hash sama; production default force_reinstall=True.
                force_reinstall=False,
            ),
            adb,
            artifacts,
            inspector,
            lambda port, token: AgentClient(port, token, config=client_config),
            repository=repository,
            registry=AgentRuntimeRegistry(),
        )
        progress_states: list[str] = []

        async def progress(_phase, _percent, _message, **fields) -> None:
            state = fields.get("bootstrap_state")
            if isinstance(state, str):
                progress_states.append(state)

        first_session = f"phase3-install-{uuid.uuid4()}"
        second_session = f"phase3-current-{uuid.uuid4()}"
        try:
            await insert_session(database, first_session, serial)
            first = await service.bootstrap(
                session_id=first_session,
                serial=serial,
                request_id="phase3-device-install",
                on_progress=progress,
            )
            first_status = service.public_status(first)
            await service.teardown(first_session, "phase3-device-install-teardown")

            await insert_session(database, second_session, serial)
            second = await service.bootstrap(
                session_id=second_session,
                serial=serial,
                request_id="phase3-device-current",
                on_progress=progress,
            )
            second_status = service.public_status(second)
            live_status = service.public_status(
                await service.status_for_device(serial, "phase3-device-status")
            )
            if second_status["install_action"] != "current":
                raise RuntimeError("Bootstrap kedua tidak memakai APK agent yang sudah sesuai.")
            if not first_status["ready"] or not second_status["ready"] or not live_status["ready"]:
                raise RuntimeError("Android agent tidak mencapai status ready.")
            return {
                "first_install_action": first_status["install_action"],
                "second_install_action": second_status["install_action"],
                "state": live_status["state"].value,
                "device_ref": live_status["device_ref"],
                "agent_version": live_status["agent_version"],
                "api_version": live_status["api_version"],
                "agent_build_sha256": live_status["agent_build_sha256"],
                "artifact_sha256": live_status["artifact_sha256"],
                "progress_states": progress_states,
            }
        finally:
            await service.teardown(second_session, "phase3-device-final-teardown")
            await service.shutdown()
            await database.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    print(json.dumps(asyncio.run(validate(arguments.serial)), indent=2, sort_keys=True))
