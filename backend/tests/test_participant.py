import pytest
from pydantic import ValidationError

from app.models.schemas import ParticipantInput
from app.services.participant import participant_dict


def test_participant_rejects_short_nik():
    with pytest.raises(ValueError, match="16 digit"):
        participant_dict(
            ParticipantInput(
                full_name="A",
                registration_no="REG-1",
                nik="123",
            )
        )


def test_participant_normalizes_registration():
    payload = participant_dict(
        ParticipantInput(full_name="Budi", registration_no=" ab-12 ", nik="1234567890123456")
    )
    assert payload["registration_no"] == "AB-12"
    assert payload["nik"] == "1234567890123456"
