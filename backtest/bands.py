"""Shared mapping between reported severity wording and the model's LOS scale."""

# Each press severity band sits at the middle of one LOS grade in eci_to_los()
# (app.py): E = 0.75–0.90, F = 0.90+, etc. Midpoints so a band never lands on a
# grade boundary.
BAND_LOS   = {"severe": "F", "heavy": "E", "moderate": "D", "light": "C"}
BAND_SCORE = {"severe": 0.93, "heavy": 0.82, "moderate": 0.68, "light": 0.50}

LOS_ORDER = "ABCDEF"


def los_distance(predicted, band):
    """0 = exact grade, 1 = one grade off, … None if band unknown."""
    target = BAND_LOS.get(band)
    if target is None or predicted not in LOS_ORDER:
        return None
    return abs(LOS_ORDER.index(predicted) - LOS_ORDER.index(target))
