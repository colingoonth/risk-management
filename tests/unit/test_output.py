"""HUMAN-mode rendering of dict payloads.

Regression guard for the `emit_success(dict, mode=HUMAN)` Python-repr leak
fixed 2026-06-07. Write-command dicts must render as a 2-col key/value table,
not `str(dict)`.
"""

from __future__ import annotations

import io

from rich.console import Console

from risk.cli.output import dict_kv_table


def render(table: object) -> str:
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=120).print(table)
    return buf.getvalue()


def test_flat_dict_renders_key_value_rows() -> None:
    out = render(dict_kv_table({"member": "alice", "role": "risk_chair", "applied": True}))
    assert "member" in out and "alice" in out
    assert "role" in out and "risk_chair" in out
    assert "applied" in out
    assert "{'" not in out  # no Python repr leak


def test_bool_renders_as_glyph() -> None:
    out = render(dict_kv_table({"flag_on": True, "flag_off": False}))
    assert "✓" in out
    assert "✗" in out
    assert "True" not in out and "False" not in out


def test_none_renders_as_em_dash() -> None:
    out = render(dict_kv_table({"missing": None}))
    assert "—" in out
    assert "None" not in out


def test_nested_dict_renders_as_json_block() -> None:
    out = render(dict_kv_table({"event": {"id": 1, "slug": "mixer"}}))
    # JSON formatting (double quotes), not Python repr (single quotes)
    assert '"slug": "mixer"' in out
    assert "{'" not in out


def test_nested_list_of_dicts_renders_as_json_block() -> None:
    out = render(dict_kv_table({"items": [{"id": 1}, {"id": 2}]}))
    assert '"id": 1' in out
    assert '"id": 2' in out
    assert "[{" not in out  # no Python repr of list-of-dicts
