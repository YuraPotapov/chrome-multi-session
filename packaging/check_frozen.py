"""Is this frozen core healthy? Reads its own --describe output.

    python3 packaging/check_frozen.py <describe.json> <expected-version>

Cheap, and it catches the two failures that are otherwise only visible to the
person who installs the package: a lazy import the spec did not list, and a
resource that did not make it into the bundle. The Windows build first shipped
without its splash for exactly the second reason, which is why this lives in one
file both builds call rather than inline in each of them.

Exits non-zero, with the problems named, if anything is wrong.
"""

import json
import sys


def problems(payload, expected_version):
    """Everything wrong with this bundle, in the order worth reading."""
    found = []
    if payload.get("version") != expected_version:
        found.append("version is %r, expected %r"
                     % (payload.get("version"), expected_version))
    if not payload.get("scenarios"):
        found.append("no scenarios: the flows tree did not make it into the bundle "
                     "(or pyyaml is missing)")
    if not payload.get("extensions"):
        found.append("no extensions: the extensions tree did not make it into the "
                     "bundle")
    for warning in payload.get("warnings", []):
        if "unavailable" in warning:
            found.append(warning)
    return found


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    # utf-8-sig, not utf-8: a describe.json written by a Windows shell may carry
    # a BOM, which json.load() refuses. Reading one is harmless where there
    # isn't one.
    with open(argv[1], encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    found = problems(payload, argv[2])
    if found:
        sys.exit("frozen core is not healthy:\n  " + "\n  ".join(found))
    print("  %d scenarios, %d extensions, chrome: %s"
          % (len(payload["scenarios"]), len(payload["extensions"]),
             payload.get("chrome", {}).get("path") or "not found"))


if __name__ == "__main__":
    main(sys.argv)
