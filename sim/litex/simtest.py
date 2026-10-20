# SPDX-License-Identifier: BSD-2-Clause
# Copyright 2026 Romain Dolbeau

"""What the tests share: running targets/sim.py with its UART on a TCP port,
and a console that types commands and waits for answers, over that port or a
board's serial port (bios_test.py), or through litex_term (l4_test.py)."""

import os
import re
import select
import signal
import socket
import subprocess
import sys
import time

TOP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ANSI = re.compile(rb"\x1b\[[0-9;]*m")
PROMPT = "litex>"

class Fail(Exception):
    pass

def check(cond, what):
    if not cond:
        raise Fail(what)

# The simulation ------------------------------------------------------------------------------------

def sim_command(variant, build, port, extra=()):
    """targets/sim.py with the BIOS, the UART on TCP port `port`."""
    return [sys.executable, os.path.join(TOP, "targets", "sim.py"),
        "--cpu-variant", variant,
        "--output-dir", build,
        "--integrated-main-ram-size", "0x10000",
        "--sim-debug",
        "--timer-uptime",                       # for the uptime command
        "--uart-tcp", "--uart-tcp-port", str(port),
        "--non-interactive", *extra]

def pacing(args):
    """The targets/sim.py options for a test's --uart-tx-clocks."""
    return ["--uart-tx-clocks", str(args.uart_tx_clocks)] if args.uart_tx_clocks else []

def venv_env():
    # The build runs tools from the venv (meson, ninja) whether it is activated or not.
    return dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""))

def start(cmd, log):
    # In a session of its own: the simulator is a grandchild, the whole group is ended.
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env=venv_env(), start_new_session=True)

def stop(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=10)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass

def listening(port):
    """True once something listens on TCP `port`, found without connecting to
    it: serial2tcp serves one client, a probe would take its place."""
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(name).read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if int(f[1].rsplit(":", 1)[1], 16) == port and f[3] == "0A":
                return True
    return False

def wait_listening(port, sim, timeout=900):
    deadline = time.time() + timeout
    while not listening(port):
        if sim.poll() is not None:
            raise Fail("the simulation ended before its UART appeared (see sim.log)")
        if time.time() > deadline:
            raise Fail("no UART on port {}".format(port))
        time.sleep(0.5)

# The console ---------------------------------------------------------------------------------------

class Console:
    """Reads what the device prints, typing commands into it. `recv(timeout)`
    returns bytes (b"" when closed, None on a time-out), `send(bytes)` types."""

    def __init__(self, recv, send, log, alive=lambda: True):
        self.recv, self.send, self.log, self.alive = recv, send, log, alive
        self.buf = ""
        self.raw = b""

    def expect(self, pattern, timeout):
        """Read until pattern (a regex) appears; return everything up to it."""
        deadline = time.time() + timeout
        while True:
            m = re.search(pattern, self.buf)
            if m:
                out, self.buf = self.buf[:m.end()], self.buf[m.end():]
                return out
            if time.time() > deadline:
                raise Fail("timeout waiting for {!r}; last output: {!r}".format(pattern, self.buf[-300:]))
            data = self.recv(max(0.1, min(1.0, deadline - time.time())))
            if data is None:
                if not self.alive():
                    raise Fail("the simulation ended; last output: {!r}".format(self.buf[-300:]))
                continue
            if not data:
                raise Fail("console closed; last output: {!r}".format(self.buf[-300:]))
            # serial2tcp sends a byte at a time: escape sequences are stripped
            # from what has accumulated, not from each piece.
            self.raw += data
            text = ANSI.sub(b"", self.raw)
            if b"\x1b" in text:            # an escape sequence not complete yet
                continue
            self.raw = b""
            text = text.decode("latin-1").replace("\r", "")
            self.log.write(text)
            self.log.flush()
            self.buf += text

    def type(self, line):
        # The device echoes as it reads; type slowly enough for a 1 MHz CPU.
        for c in line + "\n":
            self.send(c.encode())
            time.sleep(0.02)

    def command(self, cmd, timeout=120, until=PROMPT):
        self.type(cmd)
        out = self.expect(re.escape(until), timeout)
        # Drop the echoed command line.
        return out.split("\n", 1)[1] if "\n" in out else out

def socket_console(port, log, sim):
    """A console straight on the simulation's UART port."""
    wait_listening(port, sim)
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    def recv(timeout):
        sock.settimeout(timeout)
        try:
            return sock.recv(4096)
        except socket.timeout:
            return None
    return Console(recv, sock.sendall, log, alive=lambda: sim.poll() is None)

def serial_console(device, log, baudrate=115200):
    """A console on a board's serial port."""
    import serial
    port = serial.serial_for_url(device, baudrate)
    def recv(timeout):
        port.timeout = timeout
        data = port.read(max(1, port.in_waiting))
        return data if data else None
    return Console(recv, port.write, log)

def fd_console(fd, log, alive):
    """A console on a file descriptor: the master side of litex_term's terminal."""
    def recv(timeout):
        if not select.select([fd], [], [], timeout)[0]:
            return None
        try:
            return os.read(fd, 4096)
        except OSError:                 # EIO: the terminal's other side is closed
            return b""
    return Console(recv, lambda b: os.write(fd, b), log, alive=alive)
