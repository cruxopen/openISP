# SPDX-License-Identifier: MIT
"""AXI4-Lite bridge (axil_regbus) with a random master, and elaboration of
the block-design top isp_axil."""
import os

import numpy as np
import pytest

from rtl_common import rtlsim
from test_rtl_top import RTL as TOP_RTL

pytestmark = pytest.mark.skipif(not rtlsim.available(), reason="no RTL simulator")


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("axil"))
    return d, rtlsim.Sim(d, "tb_axil", ["rtl/common/axil_regbus.sv", "sim/tb_axil.sv"])


@pytest.mark.parametrize("seed,pct", [(1, 100), (2, 50), (3, 20)])
def test_axil_random(sim, seed, pct):
    d, s = sim
    rng = np.random.default_rng(seed)
    mem = {i: 0xdead0000 | i for i in range(1024)}
    ops, exp = [], []
    for _ in range(300):
        a = int(rng.integers(0, 64)) * 4 + int(rng.integers(0, 4))       # low bits ignored
        if rng.random() < 0.5:
            v = int(rng.integers(0, 1 << 32))
            ops.append(f"W {a:x} {v:x}")
            mem[a >> 2] = v
        else:
            ops.append(f"R {a:x}")
            exp.append((a, mem[a >> 2]))
    stim, out = os.path.join(d, f"stim{seed}.txt"), os.path.join(d, f"out{seed}.txt")
    with open(stim, "w") as f:
        f.write("\n".join(ops) + "\n")
    res = s.run(STIM=stim, OUT=out, SEED=seed, PCT=pct)
    assert "DONE" in res and "FAIL" not in res, res
    got = [(int(t[1], 16), int(t[2], 16)) for t in (ln.split() for ln in open(out))]
    assert got == exp


def test_isp_axil_elaborates(tmp_path):
    rtlsim.Sim(str(tmp_path), "isp_axil", TOP_RTL + ["rtl/common/axil_regbus.sv", "rtl/top/isp_axil.sv"])
