"""Validate static contracts and fixture invariants; not a runtime simulator."""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]

# 每个前缀对应一个必须存在的联调场景。新增场景不会让本检查失败，
# 但缺少任一场景、或两个文件共用同一前缀会失败。
REQUIRED_SCENARIOS = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

# 参考客户端产出的消息也要过 Schema。负例两类：未知字段、超过 16 KiB。
REF_CLIENT = ROOT / "tools" / "summon_device_ref.py"
MAX_FRAME_BYTES = 16 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_reference_client(schema):
    """跑参考客户端 --emit，把产出的消息按 Schema 校验，并验两条负例。

    返回 {True: 正例数, False: 负例数}。参考客户端是协议的可执行样板，
    它发出去的东西必须和静态样例一样过同一份 Schema。
    """
    require(REF_CLIENT.is_file(), "Missing reference client: {}".format(REF_CLIENT))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "emit.jsonl"
        # 按字节捕获：参考客户端会打印中文，Windows 默认 GBK 解码会炸。
        proc = subprocess.run(
            [sys.executable, str(REF_CLIENT), "--emit", str(out)],
            capture_output=True, timeout=120, cwd=str(ROOT),
        )
        require(proc.returncode == 0,
                "reference client --emit failed: {}".format(
                    proc.stderr.decode("utf-8", "replace").strip()[:200]))
        lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        require(lines, "reference client emitted nothing")

        wrapper = {"$schema": schema["$schema"], "$defs": schema["$defs"],
                   "$ref": "#/$defs/BridgeMessage"}
        validator = Draft202012Validator(wrapper, format_checker=FormatChecker())
        positive = 0
        for line in lines:
            errors = list(validator.iter_errors(json.loads(line)))
            require(not errors, "reference client message rejected: {}".format(
                errors[0].message if errors else ""))
            positive += 1

        # 负例一：未知字段必须被拒绝
        bad_field = json.loads(lines[0])
        bad_field["unexpected"] = "x"
        require(list(validator.iter_errors(bad_field)), "unknown field was accepted")

        # 负例二：超过 16 KiB 必须被拒绝
        oversized = json.loads(lines[0])
        oversized["payload"] = dict(oversized["payload"])
        oversized["payload"]["text"] = "x" * (MAX_FRAME_BYTES + 1024)
        require(list(validator.iter_errors(oversized)), "oversized frame was accepted")

    return {True: positive, False: 2}


def main():
    schema = json.loads((ROOT / "protocol/summon.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    fixtures = {}
    counts = {True: 0, False: 0}
    files = sorted((ROOT / "protocol/examples").glob("*.json"))
    stems = {}
    for path in files:
        key = path.stem[:2]
        require(key not in stems,
                "Duplicate scenario prefix {}: {} and {}".format(key, stems.get(key), path.name))
        stems[key] = path.name
    missing = [key for key in REQUIRED_SCENARIOS if key not in stems]
    require(not missing, "Missing required scenario files: " + ", ".join(missing))
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

    ref_counts = validate_reference_client(schema)

    links = 0
    for path in [ROOT / "README.md"] + list((ROOT / "docs").glob("*.md")) + [ROOT / "protocol/examples/README.md"]:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            target = target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            require((path.parent / unquote(parsed.path)).is_file(), "Broken link in {}: {}".format(path.name, target))
            links += 1
    print("PASS: schema; {} scenarios; {} positive and {} negative checks; fixture invariants; "
          "{} reference-client messages; {} local links.".format(
        len(files), counts[True], counts[False], ref_counts[True], links))
    print("Runtime services and physical hardware are not tested by this command.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        sys.exit(1)
