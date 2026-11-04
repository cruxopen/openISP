# SPDX-License-Identifier: MIT
"""Register map consistency: no overlapping fields, generated RTL up to date,
default configuration fits the field widths."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hwmodel import regmap                                # noqa: E402
from hwmodel.config import HwConfig                       # noqa: E402


def test_fields_do_not_overlap():
    used = {}
    for name, word, lsb, width, _ in regmap.FIELDS:
        assert word < regmap.NREG and lsb + width <= 32, name
        for bit in range(lsb, lsb + width):
            assert (word, bit) not in used, (name, used.get((word, bit)))
            used[(word, bit)] = name


def test_generated_rtl_is_current():
    with open(os.path.join(ROOT, "hw", "rtl", "top", "isp_regs.sv")) as f:
        assert f.read() == regmap.gen_sv(), "run: python -m hwmodel.regmap"


def test_default_and_4k_configs_fit():
    regmap.reg_words(HwConfig())
    regmap.reg_words(HwConfig(width=3840, height=2160))
    assert len(regmap.table_writes(HwConfig())) == 1024 + 256


def test_generated_files_are_current():
    """isp_regs.sv and the C header of the driver / firmware."""
    for rel, gen in regmap.GENERATED.items():
        with open(os.path.join(ROOT, rel)) as f:
            assert f.read() == gen(), f"{rel}: run python -m hwmodel.regmap"


def test_ctrl_block_does_not_overlap_config():
    words = {w for _, w, _, _, _ in regmap.FIELDS}
    assert max(words) < regmap.CTRL_BASE <= min(w for _, w, _, _ in regmap.CTRL_REGS)
    assert max(w for _, w, _, _ in regmap.CTRL_REGS) < regmap.GAMMA_BASE
