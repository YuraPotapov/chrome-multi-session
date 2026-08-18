"""How loaded this machine is, for the readouts on the launch page.

A deliberate mirror of ``system_load.py`` in the core, not an import of it: the
GUI depends on PySide6 and nothing else, and the two are frozen into separate
bundles - the same arrangement ``core.user_data_root`` has with
``runtime_paths``. Keep the two in step.

The core's copy explains why stall matters more than utilisation; here it is
enough to say that the numbers shown are what the load governor reads, so a user
watching this readout and the governor watching the machine never disagree.
"""

import os

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
        """Memory in use as a percentage of the total, or None.

        The parenthesis belongs around the whole ratio. Bracketing the
        subtraction instead reads back as a large negative percentage, which is
        both wrong and never over any warning threshold.
        """
        if not self.total_kb or self.available_kb is None:
            return None
        return 100.0 * (1.0 - self.available_kb / self.total_kb)

    @property
    def readable(self):
        """False on a platform this cannot measure, so callers can say so once."""
        return self.available_kb is not None or self.cpu_percent is not None


class Sampler:
    """Repeated readings. CPU percent is a rate, so it needs two to exist."""

    def __init__(self):
        self._last_cpu = None

    def read(self):
        available_kb, total_kb = self._memory()
        return Load(cpu_percent=self._cpu_percent(),
                    cpu_stall=self._stall("cpu"), mem_stall=self._stall("memory"),
                    available_kb=available_kb, total_kb=total_kb)

    @staticmethod
    def _stall(resource):
        try:
            with open("/proc/pressure/%s" % resource, encoding="utf-8") as fh:
                for line in fh:
                    if not line.startswith("some "):
                        continue
                    for field in line.split():
                        if field.startswith("avg10="):
                            return float(field[len("avg10="):])
        except (OSError, ValueError):
            return None
        return None

    @staticmethod
    def _memory():
        values = {}
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    if key in ("MemAvailable", "MemTotal"):
                        values[key] = int(rest.split()[0])
        except (OSError, ValueError, IndexError):
            return None, None
        return values.get("MemAvailable"), values.get("MemTotal")

    def _cpu_percent(self):
        try:
            with open("/proc/stat", encoding="utf-8") as fh:
                fields = [float(x) for x in fh.readline().split()[1:]]
            idle, total = fields[3], sum(fields)
        except (OSError, ValueError, IndexError):
            return None
        previous, self._last_cpu = self._last_cpu, (total, idle)
        if previous is None or total <= previous[0]:
            return None          # nothing to difference against yet
        return 100.0 * (1.0 - (idle - previous[1]) / (total - previous[0]))
