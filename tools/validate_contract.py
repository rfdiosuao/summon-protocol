"""Validate static contracts and fixture invariants; not a runtime simulator."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    schema = json.loads((ROOT / "protocol/summon.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    fixtures = {}
    counts = {True: 0, False: 0}
    files = sorted((ROOT / "protocol/examples").glob("*.json"))
    require(len(files) == 10, "Expected ten scenario files")
    for path in files:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        require(isinstance(fixture.get("description"), str), str(path))
        require(isinstance(fixture.get("checks"), list) and fixture["checks"], str(path))
        fixtures[path.stem[:2]] = fixture["checks"]
        for index, check in enumerate(fixture["checks"]):
            label = "{}[{}]".format(path.name, index)
            require(type(check["valid"]) is bool, label + ": valid must be boolean")
            require(check["schema"] in schema["$defs"], label + ": unknown definition")
            wrapper = {"$schema": schema["$schema"], "$defs": schema["$defs"],
                       "$ref": "#/$defs/" + check["schema"]}
            errors = list(Draft202012Validator(wrapper, format_checker=FormatChecker()).iter_errors(check["data"]))
            require((not errors) == check["valid"], label + ": unexpected validation result " +
                    (errors[0].message if errors else "accepted invalid payload"))
            counts[check["valid"]] += 1

    def data(key):
        return [c["data"] for c in fixtures[key] if c["valid"]]

    def types(key):
        return [v.get("type") for v in data(key)]

    flow = data("01")
    activated = types("01").index("session.activate")
    roles = {v["payload"]["role"] for v in flow[:activated] if v.get("type") == "session.ready"}
    require(roles == {"agent", "gateway"}, "Activation requires both ready messages")
    order = types("01")
    require(order.index("session.activate") < order.index("action.request") <
            order.index("action.accepted") < order.index("action.started") <
            order.index("action.completed") < order.index("input.finished"), "Invalid action fixture order")
    order = types("02")
    require(order.index("memory.updated") < order.index("session.revoke") <
            order.index("session.stopped") < order.index("session.released") <
            order.index("session.granted") < order.index("session.activate") <
            order.index("handoff.completed"), "Unsafe handoff fixture order")
    saved = next(v for v in data("02") if "memory_version" in v and "preferences" in v)
    offered = next(v["payload"]["memory"] for v in data("02") if v.get("type") == "session.offer")
    require(saved == offered, "Handoff changed persisted memory")
    requests = [v["payload"] for v in data("04") if v.get("type") == "action.request"]
    require(len(requests) == 2 and requests[0] == requests[1], "Retry changed command payload")
    require(types("04").count("action.started") == 1, "Duplicate command restarted")
    require("session.activate" not in types("05") and "session.active" not in types("05"), "Blocked handoff activated")
    require("memory.updated" not in types("06"), "Failed memory write reported success")
    failed = next(v["payload"] for v in data("06") if v.get("type") == "memory.failed")
    unchanged = next(v for v in data("06") if "memory_version" in v)
    require(failed["current_version"] == unchanged["memory_version"] == 0, "Failed write changed version")
    order = types("08")
    require(order.index("device.stop") < order.index("device.stopped"), "Stop acknowledgement precedes request")
    stale = data("10")
    require(stale[0]["lease_epoch"] < 2 and stale[1]["error"]["code"] == "STALE_LEASE", "Invalid stale-lease example")
    require(stale[2]["expires_at"] < "2026-09-23T06:00:00Z" and
            stale[3]["error"]["code"] == "EXPIRED", "Invalid expiry example")
    codes = schema["$defs"]["ErrorResponse"]["properties"]["error"]["properties"]["code"]["enum"]
    require({v["error"]["code"] for v in data("03")} == set(codes), "Error fixture coverage incomplete")

    links = 0
    for path in [ROOT / "README.md"] + list((ROOT / "docs").glob("*.md")) + [ROOT / "protocol/examples/README.md"]:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            require((path.parent / unquote(parsed.path)).is_file(), "Broken link in {}: {}".format(path.name, target))
            links += 1
    print("PASS: schema; {} scenarios; {} positive and {} negative checks; fixture invariants; {} local links.".format(
        len(files), counts[True], counts[False], links))
    print("Runtime services and physical hardware are not tested by this command.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        sys.exit(1)
