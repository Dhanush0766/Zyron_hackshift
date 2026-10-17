import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medgate.db import DB  # noqa: E402
from medgate.engine import Config, Engine  # noqa: E402


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def engine(clock):
    e = Engine(DB(":memory:"), clock, Config())
    e.ensure_device("SIM-001", "SIMULATOR")
    e.add_medication("SIM-001", "Demo tablet A", 1, unit_weight_g=0.5, weight_tol_g=0.15)
    return e
