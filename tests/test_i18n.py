import json
import re
from pathlib import Path

import match_tracker

PACKAGE = Path(match_tracker.__file__).parent
STRINGS = json.loads((PACKAGE / "static" / "i18n.json").read_text(encoding="utf-8"))
SCRIPTS = "".join(
    p.read_text(encoding="utf-8") for p in (PACKAGE / "static").glob("*.js")
)


def error_codes():
    rules = (PACKAGE / "rules.py").read_text(encoding="utf-8")
    server = (PACKAGE / "server.py").read_text(encoding="utf-8")
    return set(re.findall(r'RuleError\(\s*"([a-z_]+)"', rules)) | set(
        re.findall(r'ApiError\(\s*\d+,\s*"([a-z_]+)"', server)
    )


def test_every_language_has_the_same_keys():
    reference = set(STRINGS["en"])
    for language, strings in STRINGS.items():
        assert set(strings) == reference, language


def test_every_key_the_scripts_name_exists_and_every_plain_key_is_used():
    named = set(re.findall(r'\bt\(\s*"([a-z_0-9]+)"', SCRIPTS))
    # t(`link_${role}`) names every key that starts with "link_"
    prefixes = tuple(re.findall(r"\bt\(\s*`([a-z_0-9]+)\$\{", SCRIPTS))
    plain = {key for key in STRINGS["en"] if not key.startswith("err_")}
    assert named <= set(STRINGS["en"])
    unused = {k for k in plain - named if not (prefixes and k.startswith(prefixes))}
    assert not unused, f"never used: {sorted(unused)}"


def test_every_error_code_has_a_message_and_no_message_is_for_a_missing_code():
    codes = error_codes()
    assert codes, "no error codes found: the pattern needs updating"
    for language, strings in STRINGS.items():
        messages = {key[4:] for key in strings if key.startswith("err_")}
        assert messages == codes, language
