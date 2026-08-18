"""The machine reader: it must never guess, and never invent a number."""

import system_load


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


PRESSURE = """some avg10=12.34 avg60=8.00 avg300=3.00 total=17
full avg10=99.00 avg60=99.00 avg300=99.00 total=99
"""

MEMINFO = """MemTotal:       32610560 kB
MemFree:         1000000 kB
Buffers:          200000 kB
MemAvailable:   19855252 kB
"""


def test_stall_reads_some_not_full(tmp_path, monkeypatch):
    monkeypatch.setattr(system_load, "_PRESSURE_DIR", str(tmp_path))
    _write(tmp_path / "memory", PRESSURE)
    # `full` (every task blocked) only trips once the machine is past saving, so
    # the reader must take `some` even though `full` is the larger number here.
    assert system_load.stall("memory") == 12.34


def test_stall_is_none_without_psi(tmp_path, monkeypatch):
    monkeypatch.setattr(system_load, "_PRESSURE_DIR", str(tmp_path / "nope"))
    # None, not 0.0: a kernel without PSI has not told us the machine is calm.
    assert system_load.stall("cpu") is None


def test_memory_prefers_available_over_free(tmp_path, monkeypatch):
    monkeypatch.setattr(system_load, "_MEMINFO", _write(tmp_path / "meminfo", MEMINFO))
    available, total = system_load.memory()
    assert (available, total) == (19855252, 32610560)


def test_memory_is_none_without_proc(tmp_path, monkeypatch):
    monkeypatch.setattr(system_load, "_MEMINFO", str(tmp_path / "nope"))
    assert system_load.memory() == (None, None)


def test_used_percent_brackets_the_whole_ratio():
    load = system_load.Load(available_kb=8_000_000, total_kb=32_000_000)
    # 100 * (1 - available/total), not 100 * (1 - available) / total, which is
    # the shape that reads back as a large negative percentage.
    assert round(load.used_percent, 1) == 75.0
    assert load.used_percent >= 0


def test_cpu_percent_needs_two_readings(tmp_path, monkeypatch):
    stat = tmp_path / "stat"
    monkeypatch.setattr(system_load, "_STAT", _write(stat, "cpu 100 0 100 800 0 0 0 0\n"))
    sampler = system_load.Sampler()
    assert sampler.read().cpu_percent is None      # nothing to difference against
    # 100 more jiffies of work, 100 more idle => half the interval was busy.
    _write(stat, "cpu 200 0 100 900 0 0 0 0\n")
    assert round(sampler.read().cpu_percent) == 50


def test_a_reading_off_linux_is_all_none(tmp_path, monkeypatch):
    monkeypatch.setattr(system_load, "_STAT", str(tmp_path / "nope"))
    monkeypatch.setattr(system_load, "_MEMINFO", str(tmp_path / "nope"))
    monkeypatch.setattr(system_load, "_PRESSURE_DIR", str(tmp_path / "nope"))
    load = system_load.Sampler().read()
    assert (load.cpu_percent, load.cpu_stall, load.mem_stall,
            load.available_kb, load.used_percent) == (None,) * 5
