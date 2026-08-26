"""Map SATRIA dashboard module ids to finding source/path filters."""

from __future__ import annotations

MODULE_SOURCE_SQL: dict[str, tuple[str, tuple]] = {
    "gallery": (
        """
        AND (
            LOWER(f.source) IN (
                'gallery','dcim','download','image','video','media_image','media_video',
                'recovered_trash','recovered_cache','ios_hidden','ios_recently_deleted','ios_recovered_cache',
                'ios_deleted_metadata'
            )
            OR LOWER(f.path) LIKE '%gallery%'
            OR LOWER(f.path) LIKE '%dcim%'
            OR LOWER(f.path) LIKE '%/image/%'
            OR LOWER(f.path) LIKE '%/video/%'
        )
        """,
        (),
    ),
    "social": (
        """
        AND (
            LOWER(f.source) IN (
                'visible_ui','accessibility_visible_ui','notification',
                'instagram','facebook','x','twitter','social'
            )
            OR LOWER(f.path) LIKE '%instagram%'
            OR LOWER(f.path) LIKE '%facebook%'
            OR LOWER(f.path) LIKE '%twitter%'
            OR LOWER(f.path) LIKE '%barcelona%'
            OR LOWER(f.path) LIKE '%threads%'
            OR LOWER(f.path) LIKE '%whatsapp%'
        )
        """,
        (),
    ),
    "email": (
        """
        AND (
            LOWER(f.source) IN ('gmail','email','mail')
            OR LOWER(f.path) LIKE '%gmail%'
            OR LOWER(f.path) LIKE '%/mail/%'
        )
        """,
        (),
    ),
    "whatsapp": (
        """
        AND (
            LOWER(f.source) IN ('whatsapp','wa','msgstore')
            OR LOWER(f.path) LIKE '%whatsapp%'
            OR LOWER(f.path) LIKE '%msgstore%'
        )
        """,
        (),
    ),
    "forensic": (
        " AND 1=1 ",
        (),
    ),
}

VALID_MODULE_IDS = frozenset(MODULE_SOURCE_SQL.keys())
