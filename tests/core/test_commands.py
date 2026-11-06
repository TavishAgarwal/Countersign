from __future__ import annotations

import pytest

from countersign.core.codes import Code
from countersign.core.commands import (
    BadCommand,
    Command,
    CommandKind,
    extract_json_block,
    normalize_id,
    parse,
)


@pytest.mark.parametrize(
    "token",
    ["CS-7f3a2c", "cs-7f3a2c", "CS-7F3A2C", "7f3a2c", "7F3A2C", "`CS-7f3a2c`", "CS-7f3a2c."],
)
def test_id_variants_normalize(token: str) -> None:
    assert normalize_id(token) == "CS-7f3a2c"


def test_other_prefixes_and_8_hex() -> None:
    assert normalize_id("ap-41c0d2") == "AP-41c0d2"
    assert normalize_id("PR-9A0C11") == "PR-9a0c11"
    assert normalize_id("CS-7f3a2c91") == "CS-7f3a2c91"


@pytest.mark.parametrize(
    "token", ["", "CS-7f3a2", "CS-7f3a2cg", "XX-7f3a2c", "7f3a2c9", "CS 7f3a2c"]
)
def test_bad_ids(token: str) -> None:
    assert normalize_id(token) is None


@pytest.mark.parametrize(
    "body",
    [
        "@countersign approve CS-7f3a2c",
        "@Countersign APPROVE cs-7f3a2c",
        "  @countersign   approve   7F3A2C  ",
        "approve CS-7f3a2c",
        "hey @countersign approve CS-7f3a2c",
    ],
)
def test_approve_variants(body: str) -> None:
    assert parse(body) == Command(kind=CommandKind.APPROVE, short_id="CS-7f3a2c")


def test_callback() -> None:
    assert parse("@countersign callback CS-7f3a2c") == Command(
        kind=CommandKind.CALLBACK, short_id="CS-7f3a2c"
    )


def test_reject_with_and_without_reason() -> None:
    assert parse("@countersign reject CS-7f3a2c vendor is wrong, call back") == Command(
        kind=CommandKind.REJECT, short_id="CS-7f3a2c", reason="vendor is wrong, call back"
    )
    assert parse("@countersign reject CS-7f3a2c") == Command(
        kind=CommandKind.REJECT, short_id="CS-7f3a2c", reason=None
    )


def test_reject_reason_is_capped() -> None:
    cmd = parse("@countersign reject CS-7f3a2c " + "x" * 500)
    assert isinstance(cmd, Command) and cmd.reason is not None and len(cmd.reason) == 200


def test_status_forms() -> None:
    assert parse("@countersign status") == Command(kind=CommandKind.STATUS)
    assert parse("@countersign status cs-7f3a2c") == Command(
        kind=CommandKind.STATUS, short_id="CS-7f3a2c"
    )
    assert isinstance(parse("@countersign status nonsense"), BadCommand)


@pytest.mark.parametrize("word", ["whoami", "policy", "help", "WHOAMI"])
def test_simple_commands(word: str) -> None:
    assert parse(f"@countersign {word}") == Command(kind=CommandKind(word.lower()))


def test_autopilot_commands() -> None:
    assert parse("@countersign autopilot list") == Command(kind=CommandKind.AUTOPILOT_LIST)
    assert parse("@countersign autopilot revoke v-north") == Command(
        kind=CommandKind.AUTOPILOT_REVOKE, arg="V-NORTH"
    )
    assert isinstance(parse("@countersign autopilot revoke"), BadCommand)
    assert isinstance(parse("@countersign autopilot"), BadCommand)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "@countersign",
        "@countersign please pay everything",
        "@countersign approve",
        "@countersign approve CS-7f3a2c and CS-111111",
        "@countersign approve everything",
        "@countersign help me",
        "@countersign whoami now",
    ],
)
def test_bad_commands(body: str) -> None:
    res = parse(body)
    assert isinstance(res, BadCommand) and res.code is Code.E_BAD_COMMAND


def test_propose_carries_the_rest_of_the_message() -> None:
    body = '@countersign propose\n```json\n{"a": 1}\n```'
    cmd = parse(body)
    assert isinstance(cmd, Command) and cmd.kind is CommandKind.PROPOSE
    assert cmd.arg is not None and extract_json_block(cmd.arg) == '{"a": 1}'


def test_extract_json_block_needs_exactly_one() -> None:
    assert extract_json_block("no block") is None
    assert extract_json_block("```json\n{}\n```\n```json\n{}\n```") is None
    assert extract_json_block('text\n```json\n{"x":1}\n```\nmore') == '{"x":1}'
