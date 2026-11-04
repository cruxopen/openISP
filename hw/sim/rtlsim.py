# SPDX-License-Identifier: MIT
"""RTL simulator driver for the tests in tests/: build once per parameter set,
run many times with plusargs.

Backends (selected with OPENISP_SIM=verilator|xsim, default: the first found)
  verilator  Verilator >= 5 (--binary --timing), open source
  xsim       AMD Vivado xsim, standalone snapshot (no Tcl server)
Verilator is looked up in PATH, then in $VERILATOR_ROOT/bin."""
import os
import shutil
import subprocess

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RTL = os.path.join(HW, "rtl")
SIM = os.path.join(HW, "sim")


def _verilator():
    v = shutil.which("verilator")
    if v:
        return v
    root = os.environ.get("VERILATOR_ROOT")
    cand = os.path.join(root, "bin", "verilator") if root else None
    return cand if cand and os.path.exists(cand) else None


def _xvlog():
    return shutil.which("xvlog")


def backend():
    want = os.environ.get("OPENISP_SIM", "").lower()
    found = {"verilator": _verilator(), "xsim": _xvlog()}
    if want:
        return want if found.get(want) else None
    return next((k for k, v in found.items() if v), None)


def available():
    return backend() is not None


def _sh(cmd, cwd, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0 or "ERROR:" in p.stdout:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{p.stdout[-4000:]}")
    return p.stdout


class Sim:
    def __init__(self, workdir, top, sources, generics=None):
        self.dir = workdir
        self.kind = backend()
        if self.kind is None:
            raise RuntimeError("no RTL simulator found (verilator or xsim)")
        os.makedirs(workdir, exist_ok=True)
        srcs = [s if os.path.isabs(s) else os.path.join(HW, s) for s in sources]
        incs = sorted({os.path.dirname(s) for s in srcs})
        generics = generics or {}
        self.env = dict(os.environ)
        if self.kind == "verilator":
            vl = _verilator()
            # the conda build needs its own compiler/make next to it
            self.env["PATH"] = os.path.dirname(vl) + os.pathsep + self.env.get("PATH", "")
            gen = [f"-G{k}={v}" for k, v in generics.items()]
            _sh([vl, "--binary", "--timing", "-O3", "-Wno-fatal", "-Wno-WIDTH", "-Wno-UNUSED",
                 "-j", str(os.cpu_count() or 4), "--top-module", top, "-o", "simv",
                 "--Mdir", "obj", *[f"-I{d}" for d in incs], *gen, *srcs], workdir, self.env)
            self.exe = [os.path.join(workdir, "obj", "simv")]
            self.plus = lambda k, v: [f"+{k}={v}"]
        else:
            _sh(["xvlog", "-sv", "--nolog", *[a for d in incs for a in ("-i", d)], *srcs], workdir)
            gen = []
            for k, v in generics.items():
                gen += ["-generic_top", f"{k}={v}"]
            _sh(["xelab", "--nolog", "-debug", "off", "--standalone", *gen, top, "-s", "snap"], workdir)
            lib = os.path.join(os.path.dirname(os.path.dirname(_xvlog())), "lib", "lnx64.o")
            # the generated axsim.sh gets this path wrong in 2025.2
            self.env["LD_LIBRARY_PATH"] = f"{lib}:{lib}/Default:" + self.env.get("LD_LIBRARY_PATH", "")
            self.exe = [os.path.join(workdir, "xsim.dir", "snap", "axsim")]
            self.plus = lambda k, v: ["-testplusarg", f"{k}={v}"]

    def run(self, **plusargs):
        args = [a for k, v in plusargs.items() for a in self.plus(k, v)]
        p = subprocess.run([*self.exe, *args], cwd=self.dir, env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = [ln for ln in p.stdout.splitlines() if "TB_RESULT" in ln]
        return line[-1] if line else p.stdout[-2000:]
