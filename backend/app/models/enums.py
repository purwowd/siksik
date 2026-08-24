from __future__ import annotations

from enum import Enum


class DeviceType(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    SIMULATED = "simulated"


class AcquisitionMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class RecoveryState(str, Enum):
    SCANNING = "scanning"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Scenario(str, Enum):
    LULUS = "lulus"
    TIDAK_LULUS = "tidak_lulus"


class SessionStatus(str, Enum):
    PENDING = "pending"
    DETECTING = "detecting"
    PREPARING_AGENT = "preparing_agent"
    AWAITING_ACCESS = "awaiting_access"
    ACQUIRING = "acquiring"
    SELECTING = "selecting"
    AWAITING_REVIEW = "awaiting_review"
    INDEXING = "indexing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentBootstrapState(str, Enum):
    DETECT_DEVICE = "detect_device"
    VALIDATE_DEVICE = "validate_device"
    RESOLVE_OR_BUILD_AGENT = "resolve_or_build_agent"
    INSPECT_INSTALLED_PACKAGE = "inspect_installed_package"
    INSTALL_OR_UPDATE = "install_or_update"
    AWAITING_INSTALL_APPROVAL = "awaiting_install_approval"
    APPLY_RUNTIME_PERMISSIONS = "apply_runtime_permissions"
    AWAITING_RUNTIME_PERMISSION = "awaiting_runtime_permission"
    VERIFY_SPECIAL_ACCESS = "verify_special_access"
    AWAITING_ACCESS = "awaiting_access"
    START_AGENT = "start_agent"
    CREATE_FORWARD = "create_forward"
    AUTHENTICATE_AND_NEGOTIATE = "authenticate_and_negotiate"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Layer(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
