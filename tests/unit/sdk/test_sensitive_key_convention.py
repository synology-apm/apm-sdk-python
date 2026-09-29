"""Regression guard for `_http.py`'s `_SENSITIVE_EXACT_KEYS` debug-log-masking allowlist.

`_redact()` (see `_http.py`) masks a dict key only on an *exact* match against
`_SENSITIVE_EXACT_KEYS`, not a substring heuristic — a substring match would mask
harmless fields like the login flags "enable_syno_token"/"enable_device_token" (plain
"yes"/"no" values whose key merely contains "token"). That precision means a future
credential-shaped field can be added to a collection without ever being added to the
allowlist, and nothing else would catch it.

This test uses a deliberately broader, over-inclusive substring heuristic (the opposite
trade-off from `_redact()` itself) purely to flag every key name that is *worth a human
decision*, across every place `collections/`/`_http.py` reference a key name (dict
literal, bracket assignment/access, or `.get("key")`). Every flagged key must be a
deliberate choice: either in `_SENSITIVE_EXACT_KEYS` (masked) or in
`_REVIEWED_NON_SECRET_KEYS` below (reviewed and confirmed not to hold a credential).

Known blind spot this cannot close: a field that rides along inside a raw response
dict without SDK source ever referencing its name (e.g. the login response's
"sid"/"synotoken" — the SDK README's "Authentication Flow" section confirms only the
`id` session cookie and `data.did` are actually used, so nothing in `_http.py` ever
reads "sid"/"synotoken" by name) can't be found by scanning source for key-name
references, since the name never appears in source at all. Only endpoints whose entire
unparsed response dict reaches `_debug_print_response` are exposed to that residual
risk; today that's the login endpoint alone, and its known fields are already in
`_SENSITIVE_EXACT_KEYS`.
"""
from __future__ import annotations

import re
from pathlib import Path

import synology_apm.sdk.collections as _collections_pkg
from synology_apm.sdk._http import _SENSITIVE_EXACT_KEYS

_COLLECTIONS_DIR = Path(_collections_pkg.__file__).parent
_HTTP_PY = _COLLECTIONS_DIR.parent / "_http.py"
_SCANNED_FILES = [_HTTP_PY, *sorted(_COLLECTIONS_DIR.glob("*.py"))]

_KEY_REFERENCE = re.compile(
    r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:'          # dict literal key
    r'|\[\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\]'    # bracket assignment/access
    r'|\.get\(\s*"([A-Za-z_][A-Za-z0-9_]*)"'    # .get("key") read
)

# A false positive here just means one more entry in _REVIEWED_NON_SECRET_KEYS below.
_SUSPICIOUS_ROOT = re.compile(r"password|passwd|secret|token|key|otp|credential|cert|encrypt", re.I)
_ALWAYS_REVIEW = frozenset({"device_id"})  # opaque short name no substring root would catch

# Confirmed non-secret on review (see the field-by-field analysis this test guards):
_REVIEWED_NON_SECRET_KEYS = frozenset({
    "MISSING_LINK_KEY", "REASON_SKIPPED_FOR_NAS_ENCRYPTED_SHARED_FOLDER",  # enum-string map keys, not request/response fields
    "cert", "certificate",                                                # server's public TLS cert, not a private key
    "cloudappKeyword", "keyword",                                         # free-text search terms
    "dbCredentialOracle", "dbCredentialSql", "guestOsCredential",         # wrapper object names; the real secret is the nested "password" key, already covered
    "enableDefaultCredential", "enable_device_token", "enable_syno_token",  # boolean/flag values, not credentials
    "includeBootPartition",                                               # boolean flag; "otp" is an accidental substring of "bootpartition"
    "isEncryption",                                                       # boolean flag (is encryption enabled)
    "primKey",                                                            # tenant/domain scoping id, not a secret
    "storageEncryptionType",                                              # encryption algorithm label, not the key material
})


def _flagged_keys(files: list[Path]) -> set[str]:
    found: set[str] = set()
    for path in files:
        for m in _KEY_REFERENCE.finditer(path.read_text()):
            key = next(g for g in m.groups() if g)
            found.add(key)
    return {k for k in found if k in _ALWAYS_REVIEW or _SUSPICIOUS_ROOT.search(k)}


def test_every_suspicious_key_is_masked_or_reviewed() -> None:
    """Any key name that looks credential-shaped must be a deliberate decision (masked
    or reviewed-safe), not an oversight."""
    flagged = _flagged_keys(_SCANNED_FILES)
    unclassified = {
        k for k in flagged
        if k.lower() not in _SENSITIVE_EXACT_KEYS and k not in _REVIEWED_NON_SECRET_KEYS
    }
    assert unclassified == set(), (
        "New credential-shaped key(s) with no classification — add to "
        "_SENSITIVE_EXACT_KEYS in _http.py if secret, or to "
        "_REVIEWED_NON_SECRET_KEYS in this test if confirmed safe: "
        f"{sorted(unclassified)}"
    )


def test_reviewed_non_secret_keys_are_still_present() -> None:
    """Guard against a stale allowlist entry for a key that was renamed/removed."""
    flagged = _flagged_keys(_SCANNED_FILES)
    stale = _REVIEWED_NON_SECRET_KEYS - flagged
    assert stale == set(), f"Entries no longer found in source: {sorted(stale)}"


def test_convention_check_actually_detects_a_new_key(tmp_path: Path) -> None:
    """Guard the guard: confirm the scan can actually flag something, not pass vacuously."""
    bad_file = tmp_path / "_fake_collection.py"
    bad_file.write_text('body = {"clientApiSecretToken": value}\n')
    flagged = _flagged_keys([bad_file])
    assert flagged == {"clientApiSecretToken"}
