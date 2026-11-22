"""The reference audit must measure length and preserve angle constraints."""
import pytest
pytest.importorskip('numpy')
import numpy as np
from scripts.evaluate_traffic import coverage, mapping


def test_reference_coverage_measures_length_and_rejects_crossing_geometry():
    reference=[[0,0],[100,0]]
    parallel=[(np.array([0,3]),np.array([100,3]))]
    hit,total=coverage(reference,parallel,5)
    assert hit==total==100
    # A nearby perpendicular line must not receive credit even with a wide radius.
    crossing=[(np.array([50,-100]),np.array([50,100]))]
    hit,total=coverage(reference,crossing,100)
    assert hit==0 and total==100


def test_reference_ids_cannot_silently_overwrite_another_capture():
    with pytest.raises(ValueError,match='unique'):
        mapping(['pilot=a','pilot=b'])
