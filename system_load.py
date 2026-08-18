"""One reader for how loaded this machine is, shared by the engine and the GUI.

Utilisation is the wrong question to ask. A rig driving eight Chrome windows on
eight cores SHOULD sit at 100% CPU - that is the machine being used, not the
machine in trouble, and throttling on it means throttling always. What actually
kills a run is *stall*: tasks blocked waiting on memory reclaim until Chrome
starts swapping or the OOM killer picks a window off.

Linux answers that directly in /proc/pressure (PSI, 4.20+). ``some avg10`` is the
percentage of the last ten seconds during which at least one task was BLOCKED
waiting for a resource, which is the distinction utilisation cannot make: 100%
CPU with cpu pressure near zero is a healthy saturated machine, while 60% CPU
with memory pressure at 15 is one about to thrash.

Where PSI is not compiled in - and on macOS and Windows, which have no /proc at
all - the pressure fields read None and callers fall back to plain headroom,
which is coarser but always available. Every field is independently optional for
that reason; nothing here raises, and nothing here guesses a number it could not
read.
"""

import os

_PRESSURE_DIR = "/proc/pressure"
_STAT = "/proc/stat"
_MEMINFO = "/proc/meminfo"

KB_PER_GB = 1024 * 1024


class Load:
    """One reading. Any field is None where this platform cannot answer it."""

    __slots__ = ("cpu_percent", "cpu_stall", "mem_stall", "available_kb", "total_kb")

    def __init__(self, cpu_percent=None, cpu_stall=None, mem_stall=None,
                 available_kb=None, total_kb=None):
        self.cpu_percent = cpu_percent
        self.cpu_stall = cpu_stall
        self.mem_stall = mem_stall
        self.available_kb = available_kb
        self.total_kb = total_kb

    @property
    def used_percent(self):
        """Memory in use, as a percentage of the total, or None.

        Written once here because the expression is easy to get wrong: the
        parenthesis belongs around the whole ratio, not around the subtraction.
        """
        if not self.total_kb or self.available_kb is None:
            return None
        return 100.0 * (1.0 - self.available_kb / self.total_kb)

    @property
    def available_gb(self):
        if self.available_kb is None:
            return None
        return self.available_kb / KB_PER_GB

    def __repr__(self):
        return ("Load(cpu=%s%% cpu_stall=%s mem_stall=%s available=%s kB)"
                % (self.cpu_percent, self.cpu_stall, self.mem_stall, self.available_kb))


def stall(resource):
    """``some avg10`` for one PSI resource as a percentage, or None without PSI.

    ``some`` rather than ``full``: one blocked task is already the signal, and
    ``full`` (every task blocked) only trips once the machine is past saving.
    """
    try:
        with open(os.path.join(_PRESSURE_DIR, resource), encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("some "):
                    continue
                for field in line.split():
                    if field.startswith("avg10="):
                        return float(field[len("avg10="):])
    except (OSError, ValueError):
        return None
    return None


def memory():
    """(available_kb, total_kb), or (None, None) where /proc/meminfo is absent.

    MemAvailable, not MemFree: it is the kernel's own estimate of what a new
    process can take without pushing anything to swap, and it counts reclaimable
    page cache that MemFree writes off. On a warm machine the two differ by
    gigabytes, all of them usable.
    """
    values = {}
    try:
        with open(_MEMINFO, encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemAvailable", "MemTotal"):
                    values[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return None, None
    return values.get("MemAvailable"), values.get("MemTotal")


class Sampler:
    """Repeated readings of one machine.

    CPU percent is a rate, so it needs two readings to exist at all - that state
    is why this is a class and not a function. The first read returns None for
    it rather than a fabricated 0.
    """

    def __init__(self):
        self._last_cpu = None

    def read(self):
        available_kb, total_kb = memory()
        return Load(cpu_percent=self._cpu_percent(),
                    cpu_stall=stall("cpu"),
                    mem_stall=stall("memory"),
                    available_kb=available_kb,
                    total_kb=total_kb)

    def _cpu_percent(self):
        try:
            with open(_STAT, encoding="utf-8") as fh:
                fields = [float(x) for x in fh.readline().split()[1:]]
            idle, total = fields[3], sum(fields)
        except (OSError, ValueError, IndexError):
            return None
        previous, self._last_cpu = self._last_cpu, (total, idle)
        if previous is None or total <= previous[0]:
            return None          # nothing to difference against yet
        return 100.0 * (1.0 - (idle - previous[1]) / (total - previous[0]))
