from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from build_gemma_v2_training_package import UnionFind, split_for_group


def test_connected_families_share_one_root():
    union = UnionFind(["a", "b", "c"])
    union.union("a", "b")
    union.union("b", "c")
    assert union.find("a") == union.find("b") == union.find("c")


def test_split_assignment_is_stable_and_binary():
    first = split_for_group("leakage-example")
    assert first in {"train", "dev"}
    assert split_for_group("leakage-example") == first

