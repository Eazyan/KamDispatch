import copy
import json
from pathlib import Path
import pytest
from scripts.traffic_linux_worker import validate_plan

PLAN = json.loads((Path(__file__).resolve().parents[1] / "containers/traffic-worker/pilot.json").read_text())

def test_finite_three_tile_plan():
    assert len(validate_plan(PLAN)) == 3

@pytest.mark.parametrize("mutate", [
    lambda p: p.update(tiles=p["tiles"]*2),
    lambda p: p["tiles"][0].update(id="../escape"),
    lambda p: p["tiles"][1].update(id=p["tiles"][0]["id"]),
    lambda p: p["tiles"][0].update(center=[float("nan"),53]),
    lambda p: p.update(viewport=[100,100]),
])
def test_reject_invalid_plan_before_capture(mutate):
    plan = copy.deepcopy(PLAN)
    mutate(plan)
    with pytest.raises(ValueError):
        validate_plan(plan)

@pytest.mark.parametrize("status", [403,429])
def test_provider_rejection_is_explicit_and_not_retried(status):
    from scripts.capture_traffic import check_block, ProviderBlocked
    class Page:
        def title(self): return "Provider response"
    class Response:
        ok=False
    response=Response();response.status=status
    with pytest.raises(ProviderBlocked,match=str(status)):
        check_block(Page(),response)
