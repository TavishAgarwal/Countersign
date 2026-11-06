"""Parse and validate the policy and vendor documents; hash them; diff them (CS-F3, CS-F21)."""

from __future__ import annotations

import difflib

import yaml
from pydantic import ValidationError

from countersign.core.canon import canonical_bytes, sha256_hex
from countersign.core.models import Policy, VendorBook


class PolicyError(ValueError):
    """A policy or vendor document is malformed. The handler turns this into ``E_SELF_CHECK``."""


def _load(text: str, name: str) -> dict[str, object]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{name}: invalid YAML") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{name}: top level must be a mapping")
    return data


def parse_policy(text: str) -> Policy:
    """Parse policy.yaml text. Raises PolicyError on any malformed or unknown content."""
    try:
        return Policy.model_validate(_load(text, "policy.yaml"))
    except ValidationError as exc:
        raise PolicyError(f"policy.yaml: {_first_error(exc)}") from exc


def parse_vendors(text: str) -> VendorBook:
    """Parse vendors.yaml text. Raises PolicyError on any malformed or unknown content."""
    try:
        return VendorBook.model_validate(_load(text, "vendors.yaml"))
    except ValidationError as exc:
        raise PolicyError(f"vendors.yaml: {_first_error(exc)}") from exc


def parse_documents(policy_text: str, vendors_text: str) -> tuple[Policy, VendorBook, str]:
    """Parse both documents and return ``(policy, vendors, policy_hash)``.

    The hash is sha256 over the canonical JSON of the *validated* documents, so formatting
    and comments do not change it but any meaningful edit does. Raises PolicyError.
    """
    policy = parse_policy(policy_text)
    vendors = parse_vendors(vendors_text)
    return policy, vendors, policy_hash(policy, vendors)


def policy_hash(policy: Policy, vendors: VendorBook) -> str:
    """Hash of the validated policy and vendor documents (hex)."""
    obj = {
        "policy": policy.model_dump(mode="json"),
        "vendors": vendors.model_dump(mode="json"),
    }
    return sha256_hex(canonical_bytes(obj))


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}" if loc else str(err["msg"])


def line_diff(old_text: str, new_text: str) -> list[str]:
    """Changed lines between two document texts: ``+ line`` added, ``- line`` removed."""
    out: list[str] = []
    for line in difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(), lineterm="", n=0
    ):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append(f"{line[0]} {line[1:].strip()}")
    return out


def summarize_changes(old: Policy, new: Policy) -> list[str]:
    """Short human summary of policy changes, one string per changed key."""
    changes: list[str] = []
    for key in Policy.model_fields:
        before, after = getattr(old, key), getattr(new, key)
        if before == after:
            continue
        if isinstance(before, tuple) and all(isinstance(x, str) for x in (*before, *after)):
            added = sorted(set(after) - set(before))
            removed = sorted(set(before) - set(after))
            parts = [f"+{x}" for x in added] + [f"-{x}" for x in removed]
            changes.append(f"{key} {' '.join(parts)}")
        else:
            changes.append(f"{key} changed")
    return changes
