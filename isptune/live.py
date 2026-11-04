# SPDX-License-Identifier: MIT
"""Client of the live tuning protocol of the 3A daemon (openisp run --listen).
One command per line, answers "ok [payload]" or "err message"."""
import json
import socket


class Live:
    """host "unix:/path" connects to a Unix socket (daemon --listen unix:/path),
    otherwise TCP (e.g. through ssh -L 5555:/run/openisp/live.sock board)."""

    def __init__(self, host="127.0.0.1", port=5555, timeout=5.0):
        if host.startswith("unix:"):
            self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.s.settimeout(timeout)
            self.s.connect(host[5:])
        else:
            self.s = socket.create_connection((host, port), timeout=timeout)
        self.f = self.s.makefile("r")

    def cmd(self, line):
        self.s.sendall((line.strip() + "\n").encode())
        ans = self.f.readline().rstrip("\n")
        if not ans:
            raise ConnectionError("daemon closed the connection")
        if ans.startswith("err"):
            raise RuntimeError(ans[4:])
        return ans[3:]

    def get(self, name):
        return self.cmd(f"get {name}")

    def set(self, name, values):
        if isinstance(values, (list, tuple)):
            values = " ".join(str(v) for v in (sum(values, []) if values and isinstance(values[0], list) else values))
        return self.cmd(f"set {name} {values}")

    def state(self):
        return json.loads(self.cmd("state"))

    def stats(self):
        return json.loads(self.cmd("stats"))

    def dump(self):
        return json.loads(self.cmd("dump"))

    def push(self, tuning):
        """Send a whole tuning document (isp section incl. tables, ae, awb, af)."""
        doc = {k: tuning[k] for k in ("isp", "ae", "awb", "af") if k in tuning}
        return self.cmd("load " + json.dumps(doc, separators=(",", ":")))

    def close(self):
        self.s.close()
