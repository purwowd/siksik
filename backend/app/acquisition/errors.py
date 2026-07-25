from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    VALIDATION_ERROR = "validation_error"
    DEPENDENCY_NOT_FOUND = "dependency_not_found"
    ADB_NOT_FOUND = "adb_not_found"
    ADB_NO_DEVICE = "adb_no_device"
    ADB_MULTIPLE_DEVICES = "adb_multiple_devices"
    ADB_UNAUTHORIZED = "adb_unauthorized"
    ADB_OFFLINE = "adb_offline"
    ADB_TIMEOUT = "adb_timeout"
    ADB_COMMAND_FAILED = "adb_command_failed"
    DEVICE_UNSUPPORTED = "device_unsupported"
    DEVICE_LOCKED = "device_locked"
    DEVICE_STORAGE_LOW = "device_storage_low"
    AGENT_UNAVAILABLE = "agent_unavailable"
    AGENT_BUILD_CONFLICT = "agent_build_conflict"
    AGENT_BUILD_FAILED = "agent_build_failed"
    AGENT_BUILD_TIMEOUT = "agent_build_timeout"
    AGENT_INSTALL_FAILED = "agent_install_failed"
    AGENT_SIGNATURE_MISMATCH = "agent_signature_mismatch"
    AGENT_VERSION_MISMATCH = "agent_version_mismatch"
    AGENT_UNREACHABLE = "agent_unreachable"
    AGENT_INVALID_RESPONSE = "agent_invalid_response"
    AGENT_API_MISMATCH = "agent_api_mismatch"
    AGENT_AUTH_INVALID = "agent_auth_invalid"
    AGENT_SESSION_MISMATCH = "agent_session_mismatch"
    AWAITING_USER = "awaiting_user"
    ACCESS_DENIED = "access_denied"
    ACCESS_TIMEOUT = "access_timeout"
    RUNTIME_PERMISSION_DENIED = "runtime_permission_denied"
    RUNTIME_PERMISSION_UNSUPPORTED = "runtime_permission_unsupported"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"


ERROR_HTTP_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.VALIDATION_ERROR: 422,
    ErrorCategory.DEPENDENCY_NOT_FOUND: 424,
    ErrorCategory.ADB_NOT_FOUND: 424,
    ErrorCategory.ADB_NO_DEVICE: 503,
    ErrorCategory.ADB_MULTIPLE_DEVICES: 409,
    ErrorCategory.ADB_UNAUTHORIZED: 503,
    ErrorCategory.ADB_OFFLINE: 503,
    ErrorCategory.ADB_TIMEOUT: 504,
    ErrorCategory.ADB_COMMAND_FAILED: 502,
    ErrorCategory.DEVICE_UNSUPPORTED: 422,
    ErrorCategory.DEVICE_LOCKED: 409,
    ErrorCategory.DEVICE_STORAGE_LOW: 507,
    ErrorCategory.AGENT_UNAVAILABLE: 424,
    ErrorCategory.AGENT_BUILD_CONFLICT: 409,
    ErrorCategory.AGENT_BUILD_FAILED: 502,
    ErrorCategory.AGENT_BUILD_TIMEOUT: 504,
    ErrorCategory.AGENT_INSTALL_FAILED: 502,
    ErrorCategory.AGENT_SIGNATURE_MISMATCH: 409,
    ErrorCategory.AGENT_VERSION_MISMATCH: 409,
    ErrorCategory.AGENT_UNREACHABLE: 502,
    ErrorCategory.AGENT_INVALID_RESPONSE: 502,
    ErrorCategory.AGENT_API_MISMATCH: 409,
    ErrorCategory.AGENT_AUTH_INVALID: 401,
    ErrorCategory.AGENT_SESSION_MISMATCH: 409,
    ErrorCategory.AWAITING_USER: 409,
    ErrorCategory.ACCESS_DENIED: 409,
    ErrorCategory.ACCESS_TIMEOUT: 408,
    ErrorCategory.RUNTIME_PERMISSION_DENIED: 409,
    ErrorCategory.RUNTIME_PERMISSION_UNSUPPORTED: 422,
    ErrorCategory.STORAGE_UNAVAILABLE: 507,
    ErrorCategory.CONFLICT: 409,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.INTERNAL_ERROR: 500,
}


class AcquisitionError(RuntimeError):
    def __init__(
        self,
        category: ErrorCategory,
        public_message: str,
        *,
        retryable: bool = False,
        dependency_exit_code: int | None = None,
    ) -> None:
        super().__init__(public_message)
        self.category = category
        self.public_message = public_message
        self.retryable = retryable
        self.dependency_exit_code = dependency_exit_code

    @property
    def status_code(self) -> int:
        return ERROR_HTTP_STATUS[self.category]

    def envelope(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.category.value,
                "message": self.public_message,
                "retryable": self.retryable,
                "request_id": request_id,
            }
        }


def acquisition_error(
    category: ErrorCategory,
    public_message: str,
    *,
    retryable: bool = False,
    dependency_exit_code: int | None = None,
) -> AcquisitionError:
    return AcquisitionError(
        category,
        public_message,
        retryable=retryable,
        dependency_exit_code=dependency_exit_code,
    )
