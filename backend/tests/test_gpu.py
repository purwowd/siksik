"""GPU readiness checks untuk deploy server."""

from __future__ import annotations

import shutil
import subprocess

import pytest


def _nvidia_smi() -> tuple[bool, str]:
    if not shutil.which("nvidia-smi"):
        return False, "nvidia-smi tidak ditemukan"
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            text=True,
            timeout=10,
        )
        return True, out.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _torch_gpu() -> tuple[bool, str]:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return True, f"torch.cuda OK — {name}"
        return False, "torch terpasang tapi cuda tidak available"
    except ImportError:
        return False, "torch belum terpasang (opsional untuk PoC meta-CV)"


@pytest.mark.gpu
def test_report_gpu_status(capsys: pytest.CaptureFixture[str]):
    """Informasional: tidak gagal jika GPU absen (laptop/CPU OK untuk pipeline PoC)."""
    smi_ok, smi_msg = _nvidia_smi()
    torch_ok, torch_msg = _torch_gpu()
    print("\n=== GPU STATUS ===")
    print(f"nvidia-smi: {'OK' if smi_ok else 'NO'} — {smi_msg}")
    print(f"pytorch:    {'OK' if torch_ok else 'NO'} — {torch_msg}")
    print("==================")
    # Always pass — report only
    assert True


@pytest.mark.gpu
@pytest.mark.acceptance
def test_gpu_required_when_env_set(monkeypatch: pytest.MonkeyPatch):
    """Set SADT_REQUIRE_GPU=1 di server GPU agar deploy gagal jika CUDA tidak siap."""
    import os

    if os.getenv("SADT_REQUIRE_GPU", "0") != "1":
        pytest.skip("SADT_REQUIRE_GPU!=1 — skip gate ketat")
    smi_ok, smi_msg = _nvidia_smi()
    assert smi_ok, f"GPU wajib tapi nvidia-smi gagal: {smi_msg}"
    torch_ok, torch_msg = _torch_gpu()
    assert torch_ok, f"GPU wajib tapi PyTorch CUDA gagal: {torch_msg}"
