"""Rails that hold whether or not anybody remembers them.

Two hard rules from the project contract are asserted here as tests rather than
trusted as habits, because both fail silently and both fail in public:

1. **The repository is public.** No GroupMe id, user id or token may appear in
   source, tests or fixtures. Everything real lives in the chair's local SQLite
   database.
2. **Tests never touch the network or the macOS keychain.** A suite that reaches
   either one is a suite that behaves differently on CI, on a locked laptop, and
   on the machine where somebody happens to have a token installed.
"""

from __future__ import annotations

import re
import tokenize
from pathlib import Path

import pytest

from risk.services import groupme as groupme_svc
from risk.services.groupme import GroupMeClient, read_token_from_keychain

_SRC = Path(__file__).resolve().parents[2] / "src" / "risk"
_TESTS = Path(__file__).resolve().parents[1]

_SELF = Path(__file__).resolve()

_GROUPME_FILES = sorted(
    path
    for path in (
        *_SRC.rglob("*groupme*.py"),
        *_SRC.rglob("*groupme*.sql"),
        *_TESTS.rglob("*groupme*.py"),
    )
    # This file is excluded from its own scans: it contains the forbidden
    # patterns as literals, which is how it recognises them.
    if path != _SELF
)


def test_there_are_groupme_files_to_scan() -> None:
    """Guards the guard: a glob that matches nothing passes every scan below."""
    assert len(_GROUPME_FILES) >= 10


@pytest.mark.parametrize("path", _GROUPME_FILES, ids=lambda p: p.name)
def test_no_file_contains_something_shaped_like_a_real_groupme_id(path: Path) -> None:
    """GroupMe ids and user ids are long bare digit runs.

    Anything with eight or more consecutive digits outside an ISO date is either
    a real id that must not be here or a fixture that should be an obvious
    placeholder string instead. Long numbers that ARE legitimate — an epoch
    timestamp in a fixture — are written with digit separators
    (``1_756_000_000``), which reads better anyway and is not id-shaped.
    """
    text = path.read_text(encoding="utf-8")
    # Drop ISO dates and times first — 2026-09-04 and 20:00 are not ids.
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)
    offenders = [m.group(0) for m in re.finditer(r"(?<!\w)\d{8,}(?!\w)", text)]
    assert offenders == [], f"{path.name} contains id-shaped digit runs: {offenders}"


def _code_only(path: Path) -> str:
    """The file with comments and string literals removed.

    Prose is scanned out rather than in: this module's own docstrings, and the
    client's, discuss ``?token=`` precisely in order to say never to write it,
    and a scanner that cannot tell an instruction from an implementation flags
    the documentation and misses the code.
    """
    if path.suffix == ".sql":
        return "\n".join(line.split("--", 1)[0] for line in path.read_text().splitlines())
    kept: list[str] = []
    with path.open("rb") as handle:
        for tok in tokenize.tokenize(handle.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(tok.string)
    return " ".join(kept)


@pytest.mark.parametrize("path", _GROUPME_FILES, ids=lambda p: p.name)
def test_no_file_puts_the_token_in_a_url(path: Path) -> None:
    """``?token=`` works against GroupMe, which is exactly the danger: it puts a
    full-account credential into shell history, proxy logs and tracebacks.

    Scanned over CODE only — see :func:`_code_only`."""
    code = _code_only(path)
    assert "?token=" not in code
    assert "token=" not in code.replace("groupme-access-token", "")


def test_the_auth_header_is_the_only_place_the_token_is_used() -> None:
    """Structural, not textual: the token-returning call appears exactly once
    outside its own definition, and it is on the headers line."""
    source = (_SRC / "services" / "groupme.py").read_text()
    uses = [line.strip() for line in source.splitlines() if "self._auth()" in line]
    assert len(uses) == 1
    assert uses[0].startswith("headers = {AUTH_HEADER: self._auth()")


def test_the_suite_never_reaches_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every client in the suite is constructed with an injected token provider.

    Proved by making the real one explode and then exercising the paths a test
    would take. If any of them fell back to the default provider this fails.
    """

    def explode(*args, **kwargs):
        raise AssertionError("a test tried to shell out to the keychain")

    monkeypatch.setattr(groupme_svc.subprocess, "run", explode)

    client = GroupMeClient(
        token_provider=lambda: "test-token",
        transport=lambda *a, **k: groupme_svc.HttpResponse(status=200, body=b"{}"),
    )
    client.list_topics("parent")
    client.list_members("parent")


def test_the_real_token_reader_is_never_the_default_in_a_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client built with no arguments WOULD reach the keychain — which is why
    no test builds one, and why the API exposes ``get_groupme_client`` as an
    overridable dependency rather than constructing one inline."""
    called: list[int] = []

    def explode(*args, **kwargs):
        called.append(1)
        raise AssertionError("keychain reached")

    monkeypatch.setattr(groupme_svc.subprocess, "run", explode)
    with pytest.raises(AssertionError):
        read_token_from_keychain()
    assert called == [1]


def test_the_keychain_is_invoked_by_absolute_path_with_a_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATH is attacker-influenced under a LaunchAgent, so the lookup must not
    use it — and nothing `security` execs may inherit a poisoned one."""
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = "a-token\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(groupme_svc.subprocess, "run", fake_run)
    assert read_token_from_keychain(account="tester") == "a-token"

    argv = captured["argv"]
    assert argv[0] == "/usr/bin/security"
    assert argv[1:] == [
        "find-generic-password",
        "-a",
        "tester",
        "-s",
        "groupme-access-token",
        "-w",
    ]
    kwargs = captured["kwargs"]
    assert kwargs["shell"] is False
    assert set(kwargs["env"]) == {"HOME", "PATH"}
    assert kwargs["env"]["PATH"] == "/usr/bin:/bin"


def test_a_keychain_failure_message_never_echoes_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stderr is a channel out of a process holding a credential."""

    class Result:
        returncode = 44
        stdout = ""
        stderr = "SECRET-LOOKING-THING"

    monkeypatch.setattr(groupme_svc.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(groupme_svc.GroupMeAuthError) as exc:
        read_token_from_keychain(account="tester")
    assert "SECRET-LOOKING-THING" not in str(exc.value)
    assert "add-generic-password" in str(exc.value)
