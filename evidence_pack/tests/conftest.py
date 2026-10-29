import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def cfg():
    from evidence_pack.scoring import Config
    return Config()


@pytest.fixture(scope="session")
def packs(tmp_path_factory, cfg):
    """Build both sample packs once."""
    from evidence_pack.pipeline import build_pack
    base = tmp_path_factory.mktemp("packs")
    return {
        "fintech": build_pack(ROOT / "samples/fintech_120.yaml", base / "fintech", config=cfg),
        "nbfc": build_pack(ROOT / "samples/nbfc_12.csv", base / "nbfc", ROOT / "samples/nbfc_org.yaml", cfg),
    }
