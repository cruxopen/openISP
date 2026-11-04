# SPDX-License-Identifier: MIT
"""Python side of tb_stream.sv: pack images into beats, write the stimulus,
run the simulator, parse the output.  PPC = 2 throughout."""
import os

import numpy as np

import rtlsim

PPC = 2


def pack(fields):
    """[(value, width), ...] -> int, first field at bit 0 (two's complement)."""
    v, sh = 0, 0
    for val, w in fields:
        v |= (int(val) & ((1 << w) - 1)) << sh
        sh += w
    return v


def beats(img, bits):
    """(H, W) or (H, W, C) integer image -> list of beat ints.
    Component c of pixel p sits at bit (p*C + c)*bits."""
    a = np.asarray(img).astype(np.int64)
    if a.ndim == 2:
        a = a[..., None]
    h, w, c = a.shape
    a = a & ((1 << bits) - 1)
    flat = a.reshape(h, w // PPC, PPC * c)
    out = []
    shifts = [k * bits for k in range(PPC * c)]
    for y in range(h):
        for o in range(w // PPC):
            v = 0
            for k, s in enumerate(shifts):
                v |= int(flat[y, o, k]) << s
            out.append(v)
    return out


def unbeats(vals, h, w, bits, comps=1, signed=False):
    """Inverse of beats() for one frame."""
    a = np.zeros((h, w // PPC, PPC * comps), np.int64)
    m = (1 << bits) - 1
    for n, v in enumerate(vals):
        y, o = divmod(n, w // PPC)
        for k in range(PPC * comps):
            x = (v >> (k * bits)) & m
            if signed and x >> (bits - 1):
                x -= 1 << bits
            a[y, o, k] = x
    a = a.reshape(h, w, comps)
    return a[..., 0] if comps == 1 else a


class StreamTB:
    def __init__(self, workdir, wrap, sources, iw, ow, cw, generics=None):
        self.dir = workdir
        srcs = list(sources) + ["sim/tb_stream.sv", f"sim/wrap/{wrap}.sv"]
        g = {"IW": iw, "OW": ow, "CW": cw}
        g.update(generics or {})
        self.sim = rtlsim.Sim(workdir, "tb_stream", srcs, g)
        self.n = 0

    def run(self, frames, cfg=0, writes=(), reads=(), junk=0, vpct=100, rpct=100, seed=1,
            frame_writes=None, frame_reads=None, nout=None, idle=0):
        """frames: list of (w, h, beat list[, output beats]) - the optional
        output count (default: len(beat list)) is 0 for a truncated input frame.
        Returns (out beats [(u, l, d)],
        reads, result line).  frame_writes[i] are written before frame i
        (e.g. LUT updates); frame_reads[i] may also hold ("W", addr, data).  reads [(addr, n)] are done after the last frame
        and returned as {addr: value}; with frame_reads (list per frame) the
        reads are done after each frame and returned as a list of dicts.
        nout / idle: expected minimum output beats and the idle time that ends
        the run when the count is not known (truncated frames)."""
        self.n += 1
        stim = os.path.join(self.dir, f"stim{self.n}.txt")
        out = os.path.join(self.dir, f"out{self.n}.txt")
        rng = np.random.default_rng(seed)
        nexp = 0
        with open(stim, "w") as f:
            f.write(f"C {cfg:x}\n")
            for a, d in writes:
                f.write(f"W {a:x} {d & 0xffffffff:x}\n")
            truncated = any(len(fr) > 3 and fr[3] != len(fr[2]) for fr in frames)
            for i, fr in enumerate(frames):
                w, h, bl = fr[:3]
                for a, d in (frame_writes[i] if frame_writes else ()):
                    f.write(f"W {a:x} {d & 0xffffffff:x}\n")
                for _ in range(int(rng.integers(0, junk + 1)) if junk else 0):
                    f.write(f"J {int(rng.integers(0, 1 << 20)):x}\n")
                f.write(f"F {w} {h} {len(bl)}\n")
                f.write("".join(f"{v:x}\n" for v in bl))
                nexp += fr[3] if len(fr) > 3 else len(bl)
                if frame_reads and frame_reads[i] is not None:
                    f.write(f"E {nexp}\n" + ("Q 2000\n" if truncated else "") + "M\n")
                    for op in frame_reads[i]:
                        if op[0] == "W":           # ("W", addr, data): write between reads
                            f.write(f"W {op[1]:x} {op[2] & 0xffffffff:x}\n")
                        else:
                            f.write(f"R {op[0]:x} {op[1]}\n")
            if reads:
                f.write(f"E {nexp}\n")
                for a, n in reads:
                    f.write(f"R {a:x} {n}\n")
        res = self.sim.run(STIM=stim, OUT=out, NOUT=nexp if nout is None else nout, SEED=seed,
                           VALID_PCT=vpct, READY_PCT=rpct, IDLE=idle)
        ob, rd, rds = [], {}, []
        with open(out) as f:
            for ln in f:
                t = ln.split()
                if t[0] == "O":
                    ob.append((int(t[1]), int(t[2]), int(t[3], 16)))
                elif t[0] == "M":
                    rd = {}
                    rds.append(rd)
                else:
                    rd[int(t[1], 16)] = int(t[2], 16)
        return ob, (rds if frame_reads else rd), res


def check_stream(ob, frames_exp, res):
    """frames_exp: list of (w, h, expected beat list). Compares data, TUSER, TLAST."""
    assert "DONE" in res, res
    exp = []
    for w, h, bl in frames_exp:
        nb = w // PPC
        exp += [(int(i == 0), int(i % nb == nb - 1), v) for i, v in enumerate(bl)]
    assert len(ob) == len(exp), (len(ob), len(exp))
    for n, (g, e) in enumerate(zip(ob, exp)):
        if g != e:
            raise AssertionError(f"beat {n}: got u/l={g[:2]} {g[2]:x}, exp u/l={e[:2]} {e[2]:x}")
