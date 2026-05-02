"""Reference parser for the shell `sample_writes()` numeric-validation contract.

The plan's runtime sampler (in plans/ccbd-silent-spin-fix.md) must fail closed when
`/proc/<pid>/io` is missing fields or contains non-numeric values, never report zero.
This module documents that contract via a Python reference implementation tested here;
the shell function in the plan is required to enforce identical invariants.
"""
from __future__ import annotations

import pytest


def parse_proc_io(text: str) -> dict[str, int] | None:
    """Parse `/proc/<pid>/io` content into a dict of integer counters.

    Returns None if `wchar` or `syscw` is missing or non-numeric (fail-closed).
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip()

    required = ('wchar', 'syscw')
    parsed: dict[str, int] = {}
    for name in required:
        raw = fields.get(name)
        if raw is None or raw == '':
            return None
        if not raw.lstrip('-').isdigit():
            return None
        parsed[name] = int(raw)
    # Optional fields — record if numeric, else drop.
    for name in ('rchar', 'syscr', 'read_bytes', 'write_bytes', 'cancelled_write_bytes'):
        raw = fields.get(name)
        if raw and raw.lstrip('-').isdigit():
            parsed[name] = int(raw)
    return parsed


_VALID_PROC_IO = """\
rchar: 10252155653
wchar: 85419516
syscr: 4274593
syscw: 124778
read_bytes: 16384
write_bytes: 180965376
cancelled_write_bytes: 8192
"""


def test_valid_proc_io_parses_completely() -> None:
    result = parse_proc_io(_VALID_PROC_IO)
    assert result is not None
    assert result['wchar'] == 85419516
    assert result['syscw'] == 124778
    assert result['rchar'] == 10252155653


def test_missing_wchar_field_fails_closed() -> None:
    truncated = _VALID_PROC_IO.replace('wchar: 85419516\n', '')
    assert parse_proc_io(truncated) is None, 'missing wchar must fail closed (return None)'


def test_missing_syscw_field_fails_closed() -> None:
    truncated = _VALID_PROC_IO.replace('syscw: 124778\n', '')
    assert parse_proc_io(truncated) is None


def test_non_numeric_wchar_fails_closed() -> None:
    corrupted = _VALID_PROC_IO.replace('wchar: 85419516', 'wchar: abc')
    assert parse_proc_io(corrupted) is None, 'non-numeric must fail closed, NOT silently 0'


def test_empty_value_fails_closed() -> None:
    corrupted = _VALID_PROC_IO.replace('wchar: 85419516', 'wchar:')
    assert parse_proc_io(corrupted) is None


def test_empty_input_fails_closed() -> None:
    assert parse_proc_io('') is None


def test_zero_is_not_treated_as_missing() -> None:
    """Zero is a valid syscw value (process truly hasn't written) — must NOT trigger fail-closed."""
    text = 'wchar: 0\nsyscw: 0\n'
    result = parse_proc_io(text)
    assert result is not None
    assert result['wchar'] == 0
    assert result['syscw'] == 0


def test_partial_corruption_loses_optional_but_keeps_required() -> None:
    """Optional field corruption is tolerable; required fields must still be valid."""
    text = """\
wchar: 100
syscw: 5
rchar: garbage
"""
    result = parse_proc_io(text)
    assert result is not None
    assert result['wchar'] == 100
    assert result['syscw'] == 5
    assert 'rchar' not in result
