from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from pv.schemas import AEReport, format_errors

GOOD = dict(reporter_type="physician", patient_age_years=54, patient_sex="F", suspect_drug="Amoxicillin",
            reaction_description="Patient developed severe rash and swelling of the face",
            onset_date=str(date.today() - timedelta(days=2)))


def test_valid():
    assert AEReport.model_validate(GOOD).suspect_drug == "Amoxicillin"


@pytest.mark.parametrize("patch,field", [
    ({"patient_age_years": -1}, "patient_age_years"),
    ({"patient_age_years": 200}, "patient_age_years"),
    ({"onset_date": str(date.today() + timedelta(days=3))}, "onset_date"),
    ({"reaction_description": "rash"}, "reaction_description"),
    ({"reaction_description": "bad\x00bytes in this text"}, "reaction_description"),
    ({"reporter_type": "alien"}, "reporter_type"),
    ({"unexpected": 1}, "unexpected"),
    ({"concomitant_drugs": ["x"]}, "concomitant_drugs"),
])
def test_invalid(patch, field):
    with pytest.raises(ValidationError) as ei:
        AEReport.model_validate({**GOOD, **patch})
    assert field in [e["field"].split(".")[0] for e in format_errors(ei.value)]


def test_errors_do_not_echo_input():
    with pytest.raises(ValidationError) as ei:
        AEReport.model_validate({**GOOD, "reaction_description": "SECRET-NAME-123\x00"})
    assert "SECRET-NAME-123" not in str(format_errors(ei.value))
