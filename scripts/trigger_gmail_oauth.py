#!/usr/bin/env python3
"""Manual Gmail OAuth probe via live Android agent loopback API."""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# WSL: agent loopback forward binds Windows host IP, not 127.0.0.1
if not os.environ.get("SADT_AGENT_FORWARD_HOST"):
    try:
        import subprocess

        gateway = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True,
        ).split()[2]
        os.environ["SADT_AGENT_FORWARD_HOST"] = gateway
    except Exception:
        pass

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.agent_client import AgentClient, AgentClientConfig
from app.core.config import settings


async def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger Gmail OAuth on connected Android agent")
    parser.add_argument("--serial", default=None, help="ADB serial (default: first device)")
    parser.add_argument("--account", default=None, help="Google account email (default: first discovered)")
    parser.add_argument("--poll-seconds", type=int, default=90, help="Max wait for user consent on device")
    args = parser.parse_args()

    if not settings.gmail_client_id.strip():
        print("ERROR: SADT_GMAIL_CLIENT_ID kosong — isi di .env terlebih dahulu.")
        return 1

    adb = AsyncAdbTransport(timeout_seconds=30)
    listed = await adb.list_devices()
    devices = [d.serial for d in listed if d.state == "device"]
    if not devices:
        print("ERROR: Tidak ada device ADB terhubung.")
        return 1
    serial = args.serial or devices[0]
    if serial not in devices:
        print(f"ERROR: Serial {serial!r} tidak ditemukan. Devices: {devices}")
        return 1

    session_id = f"oauth-probe-{uuid.uuid4()}"
    token = secrets.token_urlsafe(48)
    expires_at_ms = int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp() * 1000)
    component = settings.android_agent_component
    device_port = settings.android_agent_device_port

    print(f"Device: {serial}")
    print(f"Session: {session_id}")
    print(f"Gmail client_id: {settings.gmail_client_id[:24]}...")

    await adb.start_activity(
        serial,
        component,
        {
            "session_id": session_id,
            "session_token": token,
            "token_expires_at_epoch_ms": expires_at_ms,
        },
    )

    port = await adb.create_forward(serial, device_port)
    client = AgentClient(port, token, config=AgentClientConfig(timeout_seconds=15, max_attempts=5))

    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        try:
            health = await client.health()
            print(f"Agent health: {health.body.state} (agent {health.body.agent_version})")
            break
        except Exception as exc:
            print(f"Menunggu agent... ({exc.__class__.__name__}: {exc})")
            await asyncio.sleep(1.5)
    else:
        print("ERROR: Agent loopback tidak merespons dalam 45 detik.")
        return 1

    accounts = await client.list_google_accounts(session_id)
    if not accounts:
        print("ERROR: Tidak ada akun Google ditemukan di device.")
        return 1
    account = args.account or accounts[0].name
    print(f"Akun Google: {account}")
    if args.account and account != args.account:
        print(f"WARNING: memakai {account!r} (requested {args.account!r})")

    print("\n>>> CEK HP: setujui dialog OAuth Gmail jika muncul <<<\n")

    poll_deadline = time.monotonic() + args.poll_seconds
    last_error: str | None = None
    while time.monotonic() < poll_deadline:
        auth_token = await client.get_google_auth_token(
            session_id,
            account,
            scope=settings.resolved_gmail_scope,
        )
        if auth_token:
            preview = auth_token[:12] + "..." + auth_token[-6:] if len(auth_token) > 24 else auth_token
            print(f"SUCCESS: Gmail OAuth token didapat ({len(auth_token)} chars): {preview}")
            return 0
        last_error = "token_unavailable"
        await asyncio.sleep(3.0)

    print(f"ERROR: OAuth gagal setelah {args.poll_seconds}s (last: {last_error}).")
    print("Cek: SHA-1 Android OAuth client = cert APK terinstall, package com.siksik.agent")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
