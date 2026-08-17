"""Unit tests for selective iOS SMS/iMessage/contacts backup parsing."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.acquisition.ios_backup_comms import (
    _apple_ts_to_iso,
    _limit_for_mode,
    _message_record,
    _contact_record,
    parse_contacts_db,
    parse_messages_db,
)
from app.models.schemas import AcquisitionMode


def _make_sms_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            handle_id INTEGER,
            text TEXT,
            attributedBody BLOB,
            is_from_me INTEGER,
            date INTEGER,
            service TEXT
        );
        INSERT INTO handle VALUES (1, '+62811111111', 'SMS');
        INSERT INTO handle VALUES (2, 'friend@icloud.com', 'iMessage');
        INSERT INTO message VALUES (10, 1, 'halo sms', NULL, 0, 700000000, 'SMS');
        INSERT INTO message VALUES (11, 2, 'halo imessage', NULL, 1, 700000100, 'iMessage');
        """
    )
    con.close()


def _make_contacts_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE ABPerson (
            ROWID INTEGER PRIMARY KEY,
            First TEXT, Last TEXT, Middle TEXT, Organization TEXT,
            CreationDate REAL, ModificationDate REAL
        );
        CREATE TABLE ABMultiValue (
            ROWID INTEGER PRIMARY KEY,
            record_id INTEGER, property INTEGER, label TEXT, value TEXT
        );
        INSERT INTO ABPerson VALUES (1, 'Ada', 'Lovelace', NULL, NULL, 0, 700000000);
        INSERT INTO ABMultiValue VALUES (1, 1, 3, NULL, '+6281222333');
        INSERT INTO ABMultiValue VALUES (2, 1, 4, NULL, 'ada@example.com');
        """
    )
    con.close()


@pytest.mark.unit
def test_limit_for_mode_full_zero_is_unlimited() -> None:
    assert _limit_for_mode(AcquisitionMode.QUICK, 50, 0) == 50
    assert _limit_for_mode(AcquisitionMode.FULL, 50, 0) is None
    assert _limit_for_mode(AcquisitionMode.FULL, 50, 100) == 100


@pytest.mark.unit
def test_apple_ts_to_iso_seconds() -> None:
    # 700000000 cocoa seconds ≈ 2023-03-08
    iso = _apple_ts_to_iso(700_000_000)
    assert iso is not None
    assert iso.endswith("Z")
    assert iso.startswith("2023-")


@pytest.mark.unit
def test_parse_messages_distinguishes_sms_and_imessage(tmp_path: Path) -> None:
    db_path = tmp_path / "sms.db"
    _make_sms_db(db_path)
    items = parse_messages_db(db_path, limit=10)
    assert len(items) == 2
    by_service = {i["service"]: i for i in items}
    assert "sms" in by_service
    assert "imessage" in by_service
    assert "halo sms" in by_service["sms"]["normalized_text"]
    assert "[iMessage]" in by_service["imessage"]["normalized_text"]

    capped = parse_messages_db(db_path, limit=1)
    assert len(capped) == 1


@pytest.mark.unit
def test_parse_messages_applies_not_before_cutoff(tmp_path: Path) -> None:
    db_path = tmp_path / "sms.db"
    _make_sms_db(db_path)
    kept = parse_messages_db(db_path, limit=None, not_before_epoch_s=1_600_000_000)
    dropped = parse_messages_db(db_path, limit=None, not_before_epoch_s=1_900_000_000)
    assert len(kept) == 2
    assert dropped == []


@pytest.mark.unit
def test_parse_contacts_and_inventory_records(tmp_path: Path) -> None:
    db_path = tmp_path / "AddressBook.sqlitedb"
    _make_contacts_db(db_path)
    items = parse_contacts_db(db_path, limit=None)
    assert len(items) == 1
    assert items[0]["display_name"] == "Ada Lovelace"
    assert items[0]["phones"][0]["value"] == "+6281222333"

    contact = _contact_record(
        session_id="session01", crawl_id="crawl_id_01", item=items[0]
    )
    assert contact.source_kind == "contact"
    assert contact.provenance.source_adapter == "ios_backup_contacts"
    assert contact.provenance.enumeration_method == "ios_mobilebackup2"

    sms_path = tmp_path / "sms.db"
    _make_sms_db(sms_path)
    msg_items = parse_messages_db(sms_path, limit=None)
    msg = _message_record(
        session_id="session01", crawl_id="crawl_id_01", item=msg_items[0]
    )
    assert msg.source_kind == "sms"
    assert msg.provenance.source_adapter == "ios_backup_messages"
