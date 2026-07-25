from __future__ import annotations

import uuid


def stable_file_id(session_id: str, relative_path: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"siksik://session/{session_id}/file/{relative_path}",
        )
    )
