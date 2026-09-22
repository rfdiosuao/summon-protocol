#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SUMMON 设备端参考实现（Device Reference Client）。

这是协议的最小可读实现，用途有两个：

1. **P0 的模拟壳**：Hub 与前端在没有真实固件时，用它顶替一台设备。
2. **移植样板**：第三方照搬 ``DeviceClient`` 的所有权模型，只需实现自己的 Port。

本文件只依赖 Python 标准库即可跑通全部自检（``--case`` 子用例走进程内
LoopbackLink，不经过网络）。只有 ``--connect`` 连接真实 Gateway 时才需要
``websockets``。

安全规则在这里是**框架行为**，不是 Port 的自觉——Port 想跳过也跳不掉：

* 所有输出只收一个完成回调，因此阻塞式渲染/播放必须另起工作任务；
* ``stop`` 之后必须等 Port 确认真的停稳，才发 ``device.stopped``；
* ``command_id`` 去重、租约与截止时间校验、心跳超时即停、重连不重放，
  全部由本模块持有，Port 接触不到这些代码。

本模板**不提供物理急停**。急停是硬件，框架只能上报 ESTOP。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "protocol" / "summon.schema.json"

#: 契约规定的控制帧上限（docs/PROTOCOL.md 第 4 节）。缓冲容量按它推导，
#: 不要用一个小固定值去解析可能达到上限的响应。
MAX_FRAME_BYTES = 16 * 1024

HEARTBEAT_INTERVAL_S = 1.0
HEARTBEAT_TIMEOUT_S = 3.0
DEDUP_RETENTION_S = 24 * 3600


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


class ProtocolError(Exception):
    """带稳定错误码的协议错误。程序分支只判断 code。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def canonical_hash(obj) -> str:
    """规范化 JSON 的稳定散列，用于判断"同 ID 同内容"。"""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class BridgeCodec:
    """BridgeMessage 编解码。容量按 MAX_FRAME_BYTES 推导，超限直接拒绝。"""

    TYPES = (
        "device.hello",
        "device.heartbeat",
        "device.command",
        "device.result",
        "device.stop",
        "device.stopped",
    )

    def __init__(self, max_bytes: int = MAX_FRAME_BYTES):
        self.max_bytes = max_bytes

    def encode(self, mtype: str, payload: dict) -> bytes:
        if mtype not in self.TYPES:
            raise ProtocolError("INVALID_MESSAGE", "未知消息类型: {}".format(mtype))
        envelope = {
            "v": 1,
            "message_id": new_id("message"),
            "sent_at": utcnow(),
            "type": mtype,
            "payload": payload,
        }
        return self.dumps(envelope)

    def dumps(self, envelope: dict) -> bytes:
        raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ProtocolError("INVALID_MESSAGE", "帧超过 {} 字节上限".format(self.max_bytes))
        return raw

    def decode(self, raw: bytes) -> dict:
        if len(raw) > self.max_bytes:
            raise ProtocolError("INVALID_MESSAGE", "帧超过 {} 字节上限".format(self.max_bytes))
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("INVALID_MESSAGE", "JSON 解析失败: {}".format(exc))
        self.check(envelope)
        return envelope

    @staticmethod
    def check(envelope: dict) -> None:
        if not isinstance(envelope, dict):
            raise ProtocolError("INVALID_MESSAGE", "信封必须是对象")
        if envelope.get("v") != 1:
            raise ProtocolError("UNSUPPORTED_VERSION", "wire 版本不是 1")
        mtype = envelope.get("type")
        if mtype not in BridgeCodec.TYPES:
            raise ProtocolError("INVALID_MESSAGE", "未知消息类型: {!r}".format(mtype))
        for key in ("message_id", "sent_at", "payload"):
            if key not in envelope:
                raise ProtocolError("INVALID_MESSAGE", "缺少字段 {}".format(key))
        if not isinstance(envelope["payload"], dict):
            raise ProtocolError("INVALID_MESSAGE", "payload 必须是对象")
        # v1 拒绝未知字段：多一个键就当无效，不静默忽略。
        allowed = {"v", "message_id", "sent_at", "type", "payload"}
        extra = set(envelope) - allowed
        if extra:
            raise ProtocolError("INVALID_MESSAGE", "未知字段: {}".format(sorted(extra)))


class DedupTable:
    """command_id 去重表。

    同 ID 同内容 -> 返回已有状态，不产生第二次执行。
    同 ID 不同内容 -> IDEMPOTENCY_CONFLICT。
    """

    def __init__(self, retention_s: int = DEDUP_RETENTION_S):
        self.retention_s = retention_s
        self._rows = {}

    def lookup(self, command_id: str, payload_hash: str):
        row = self._rows.get(command_id)
        if row is None:
            return None
        if row["payload_hash"] != payload_hash:
            raise ProtocolError("IDEMPOTENCY_CONFLICT", "command_id 已绑定不同内容")
        return row

    def remember(self, command_id: str, payload_hash: str, state: str) -> None:
        self._rows[command_id] = {
            "payload_hash": payload_hash,
            "state": state,
            "at": datetime.now(timezone.utc),
        }
        self._evict()

    def _evict(self) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - self.retention_s
        for key in [k for k, v in self._rows.items() if v["at"].timestamp() < cutoff]:
            del self._rows[key]


class Lease:
    """租约校验：旧 epoch 与过期动作一律拒绝，不运动。"""

    def __init__(self):
        self.epoch = 0

    def install(self, epoch: int) -> None:
        if epoch <= self.epoch:
            raise ProtocolError("STALE_LEASE", "lease_epoch {} 旧于当前 {}".format(epoch, self.epoch))
        self.epoch = epoch

    def check_command(self, lease_epoch: int, expires_at: str) -> None:
        if lease_epoch < self.epoch:
            raise ProtocolError("STALE_LEASE", "lease_epoch {} 旧于当前 {}".format(lease_epoch, self.epoch))
        if _parse_utc(expires_at) <= datetime.now(timezone.utc):
            raise ProtocolError("EXPIRED", "动作已于 {} 过期".format(expires_at))
        # 更新到的 epoch 立即成为当前值；旧的从此不可执行
        if lease_epoch > self.epoch:
            self.epoch = lease_epoch


def _parse_utc(text: str) -> datetime:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ProtocolError("INVALID_MESSAGE", "时间格式非法: {}".format(exc))


class Port:
    """第三方移植时唯一需要实现的接口。

    约定（由签名强制，不靠自觉）：

    * 三个输出函数**没有同步返回值**，只能通过 ``done`` 回调报告完成，
      因此阻塞式渲染/播放必须另起工作任务——本签名不允许同步等待。
    * ``stop_all`` 也要回调，因此 ``device.stopped`` 只能在实际停稳后发出，
      无法用计时器伪造完成。
    * ``on_identity`` 只负责投递，不做 DNS/网络/解码。
    """

    #: 能力声明。实际使用取本声明与本地允许动作的交集。
    capabilities = ()

    def display_text(self, text: str, done) -> None:
        raise NotImplementedError

    def audio_pcm(self, pcm: bytes, hz: int, bits: int, ch: int, done) -> None:
        raise NotImplementedError

    def gesture(self, name: str, repeat: int, done) -> None:
        raise NotImplementedError

    def on_identity(self, address_hint: str) -> None:
        raise NotImplementedError

    def stop_all(self, done) -> None:
        raise NotImplementedError


def _finish(done, status="COMPLETED", detail=""):
    if done is not None:
        done(status, detail)


class NullPort(Port):
    """无硬件端口：把动作打印到 stdout。用于自检与 CI。"""

    capabilities = ("display.text",)

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.stopped = False

    def _log(self, text):
        if self.verbose:
            print("    [port] {}".format(text))

    def display_text(self, text, done):
        self._log("display.text -> {!r}".format(text))
        _finish(done, "COMPLETED", "rendered")

    def audio_pcm(self, pcm, hz, bits, ch, done):
        self._log("audio_pcm {}B {}Hz/{}bit/{}ch".format(len(pcm), hz, bits, ch))
        _finish(done, "COMPLETED", "played")

    def gesture(self, name, repeat, done):
        self._log("gesture {} x{}".format(name, repeat))
        _finish(done, "COMPLETED", "moved")

    def on_identity(self, address_hint):
        self._log("identity <- {!r}".format(address_hint))

    def stop_all(self, done):
        self.stopped = True
        self._log("stop_all -> 已停止")
        _finish(done, "COMPLETED", "stopped")


class CliPort(NullPort):
    """终端端口：键盘当按键、终端当屏幕。交互试用用。"""

    capabilities = ("display.text",)


class StopFsm:
    """停止状态机：清队列 -> 等 Port 确认停稳 -> 才允许发 device.stopped。"""

    def __init__(self, port: Port):
        self.port = port
        self.pending = []
        self.stopping = False
        self.stopped_confirmed = False

    def enqueue(self, item) -> None:
        if self.stopping:
            return
        self.pending.append(item)

    def clear(self) -> int:
        count = len(self.pending)
        self.pending = []
        return count

    async def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        cleared = self.clear()
        fut = asyncio.get_running_loop().create_future()

        def done(status, detail):
            if not fut.done():
                fut.set_result((status, detail))

        # Port 必须真的停下来才回调；框架据此才发 device.stopped。
        self.port.stop_all(done)
        status, detail = await fut
        self.stopped_confirmed = status == "COMPLETED"
        return cleared, status, detail


class DeviceClient:
    """设备端主体。持有 WSS、去重、租约、心跳与停止状态机。"""

    def __init__(self, device_id: str, shell_id: str, port: Port, link=None, verbose=True,
                 heartbeat_timeout: float = HEARTBEAT_TIMEOUT_S):
        self.device_id = device_id
        self.shell_id = shell_id
        self.port = port
        self.link = link
        self.verbose = verbose
        self.heartbeat_timeout = heartbeat_timeout
        self.codec = BridgeCodec()
        self.dedup = DedupTable()
        self.lease = Lease()
        self.stop_fsm = StopFsm(port)
        self.inbox = asyncio.Queue()
        self.connected = False
        self.last_gateway_heartbeat = None
        self.executed = []          # 已执行动作的记录，供自检断言
        self.unconfirmed = {}       # command_id -> payload 散列，重连后报 UNKNOWN
        self.emitted = []           # --emit 用
        self.received = []          # --emit 用

    # ---------- 发送 ----------

    async def send(self, mtype: str, payload: dict) -> None:
        raw = self.codec.encode(mtype, payload)
        self.emitted.append(json.loads(raw.decode("utf-8")))
        if self.verbose:
            print("  -> {}".format(mtype))
        if self.link is not None:
            await self.link.send_to_gateway(raw)

    async def _send_result(self, command_id: str, status: str, detail: str) -> None:
        await self.send("device.result", {
            "device_id": self.device_id,
            "command_id": command_id,
            "status": status,
            "detail": detail,
        })

    # ---------- 接收 ----------

    async def feed(self, raw: bytes) -> None:
        """注入一帧（自检用）。连接模式下由 reader 调用。"""
        try:
            envelope = self.codec.decode(raw)
        except ProtocolError as exc:
            await self.send("device.result", {
                "device_id": self.device_id,
                "command_id": "unknown",
                "status": "FAILED",
                "detail": "{}: {}".format(exc.code, exc.message),
            })
            return
        self.received.append(envelope)
        await self.handle(envelope)

    async def handle(self, envelope: dict) -> None:
        mtype = envelope["type"]
        payload = envelope["payload"]
        if mtype == "device.command":
            await self._on_command(payload)
        elif mtype == "device.stop":
            await self._on_stop(payload)
        elif mtype == "device.heartbeat":
            self.last_gateway_heartbeat = datetime.now(timezone.utc)
        else:
            raise ProtocolError("INVALID_MESSAGE", "设备端不应收到 {}".format(mtype))

    async def _on_command(self, payload: dict) -> None:
        command_id = payload["command_id"]
        digest = canonical_hash(payload)

        # 1) 身份与幂等先于 seq 与租约比较
        try:
            prior = self.dedup.lookup(command_id, digest)
        except ProtocolError as exc:
            # 同 ID 不同内容：拒绝并回报，不运动、不覆盖已有记录
            await self._send_result(command_id, "FAILED",
                                    "{}: {}".format(exc.code, exc.message))
            return
        if prior is not None:
            # 同 ID 同内容：返回已有状态，绝不再次运动
            self.executed.append({"command_id": command_id, "dedup_hit": True})
            await self._send_result(command_id, prior["state"], "dedup hit")
            return

        # 2) 租约与截止时间
        try:
            self.lease.check_command(payload["lease_epoch"], payload["expires_at"])
        except ProtocolError as exc:
            await self._send_result(command_id, "FAILED", "{}: {}".format(exc.code, exc.message))
            return

        if self.stop_fsm.stopping:
            await self._send_result(command_id, "FAILED", "STOPPING: 正在停止，拒绝新动作")
            return

        # 3) 记录后执行；重启/重连后未确认的一律 UNKNOWN，不自动重放
        self.dedup.remember(command_id, digest, "EXECUTING")
        self.unconfirmed[command_id] = digest
        self.executed.append({"command_id": command_id, "dedup_hit": False})
        self.stop_fsm.enqueue(command_id)
        await self._execute(command_id, payload["action"], digest)

    async def _execute(self, command_id: str, action: dict, digest: str) -> None:
        cap = action.get("capability")
        args = action.get("args", {})
        fut = asyncio.get_running_loop().create_future()

        def done(status, detail):
            if not fut.done():
                fut.set_result((status, detail))

        if cap == "display.text":
            self.port.display_text(args.get("text", ""), done)
        elif cap == "speech.say":
            self.port.audio_pcm(args.get("pcm", b""), args.get("hz", 16000),
                                args.get("bits", 16), args.get("ch", 1), done)
        elif cap == "arm.gesture":
            self.port.gesture(args.get("name", ""), args.get("repeat", 1), done)
        else:
            await self._send_result(command_id, "FAILED", "CAPABILITY_UNSUPPORTED: {}".format(cap))
            return

        status, detail = await fut
        self.unconfirmed.pop(command_id, None)
        self.dedup.remember(command_id, digest, status)
        await self._send_result(command_id, status, detail)

    async def _on_stop(self, payload: dict) -> None:
        epoch = payload.get("lease_epoch")
        if epoch is not None and epoch < self.lease.epoch:
            await self.send("device.stopped", {
                "device_id": self.device_id,
                "session_id": payload.get("session_id", ""),
                "lease_epoch": self.lease.epoch,
            })
            return
        await self.stop_fsm.stop()
        # 未确认的命令在停止后报 UNKNOWN，绝不自动重放
        for command_id, digest in list(self.unconfirmed.items()):
            self.dedup.remember(command_id, digest, "UNKNOWN")
        self.unconfirmed.clear()
        await self.send("device.stopped", {
            "device_id": self.device_id,
            "session_id": payload.get("session_id", ""),
            "lease_epoch": self.lease.epoch,
        })

    async def hello(self) -> None:
        await self.send("device.hello", {"device_id": self.device_id, "shell_id": self.shell_id})
        self.connected = True

    async def heartbeat_once(self) -> None:
        await self.send("device.heartbeat", {"device_id": self.device_id})

    def heartbeat_overdue(self) -> bool:
        if self.last_gateway_heartbeat is None:
            return False
        delta = (datetime.now(timezone.utc) - self.last_gateway_heartbeat).total_seconds()
        return delta > self.heartbeat_timeout

    async def reconnect(self) -> None:
        """断线重连：重新握手；未确认命令报 UNKNOWN，绝不自动重放。"""
        self.connected = False
        for command_id, digest in list(self.unconfirmed.items()):
            self.dedup.remember(command_id, digest, "UNKNOWN")
            await self._send_result(command_id, "UNKNOWN", "reconnect: 结果未确认，需现场核对")
        self.unconfirmed.clear()
        self.stop_fsm.stopping = False
        self.stop_fsm.stopped_confirmed = False
        await self.hello()

    async def run_heartbeat_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.heartbeat_once()
            # 3 秒收不到 Gateway 心跳：本地停止并清空待执行队列
            if self.heartbeat_overdue() and not self.stop_fsm.stopping:
                await self.stop_fsm.stop()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_S)
            except asyncio.TimeoutError:
                pass


class LoopbackLink:
    """进程内双向链路。自检走这里，不经过网络，因此没有时序抖动。"""

    def __init__(self):
        self.to_gateway = asyncio.Queue()
        self.to_device = asyncio.Queue()

    async def send_to_gateway(self, raw: bytes) -> None:
        await self.to_gateway.put(raw)

    async def send_to_device(self, raw: bytes) -> None:
        await self.to_device.put(raw)


class StubGateway:
    """本地假 Gateway：只发 device.command / device.stop / device.heartbeat。

    同时记录双向消息，自检据此断言，不必自己拼队列。
    """

    def __init__(self, link: LoopbackLink, device_id: str):
        self.link = link
        self.device_id = device_id
        self.codec = BridgeCodec()
        self.received = []
        self.sent = []

    async def send(self, mtype: str, payload: dict) -> None:
        raw = self.codec.encode(mtype, payload)
        self.sent.append(json.loads(raw.decode("utf-8")))
        await self.link.send_to_device(raw)

    async def drain(self):
        """取走设备端发来的所有消息。"""
        out = []
        while not self.link.to_gateway.empty():
            out.append(json.loads((await self.link.to_gateway.get()).decode("utf-8")))
        return out

    async def deliver(self, raw: bytes) -> None:
        await self.link.send_to_device(raw)


def future_ts(seconds: float) -> str:
    return datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + seconds, timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def past_ts(seconds: float) -> str:
    return future_ts(-seconds)


def command_payload(command_id, session_id, epoch, expires_at, action):
    return {
        "command_id": command_id,
        "session_id": session_id,
        "lease_epoch": epoch,
        "expires_at": expires_at,
        "action": action,
    }


def display_action(text):
    return {"capability": "display.text", "args": {"text": text}}


def make_client(link, port=None, **kw):
    return DeviceClient("passport_a", "shell_a", port or NullPort(verbose=False),
                        link=link, verbose=False, **kw)


async def deliver(client, raw):
    await client.feed(raw)


def types_of(messages):
    return [m["type"] for m in messages]


# ---------------------------------------------------------------- 子用例

async def case_full_sequence():
    link = LoopbackLink()
    client = make_client(link)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    await gw.send("device.command", command_payload(
        "command_1", "session_a", 1, future_ts(5), display_action("这是第一个展品。")))
    await deliver(client, await link.to_device.get())
    await gw.send("device.stop", {"session_id": "session_a", "lease_epoch": 1})
    await deliver(client, await link.to_device.get())
    out = await gw.drain()
    seq = types_of(gw.sent) + types_of(out)
    for want in ("device.hello", "device.command", "device.result",
                 "device.stop", "device.stopped"):
        assert want in seq, "full_sequence 缺少 {}".format(want)
    real = [e for e in client.executed if not e["dedup_hit"]]
    assert len(real) == 1, "full_sequence 执行次数应为 1，实际 {}".format(len(real))
    assert client.lease.epoch == 1
    return "full_sequence 通过：hello/command/result/stop/stopped 齐全，执行 1 次"


async def case_dedup():
    link = LoopbackLink()
    client = make_client(link)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    payload = command_payload("command_1", "session_a", 1, future_ts(5), display_action("同一句"))
    await gw.send("device.command", payload)
    await deliver(client, await link.to_device.get())
    await gw.send("device.command", payload)          # 同 ID 同内容重试
    await deliver(client, await link.to_device.get())
    out = await gw.drain()
    results = [m for m in out if m["type"] == "device.result"]
    assert len(results) == 2, "应有一次执行加一次去重命中，实际 {} 条".format(len(results))
    real = [e for e in client.executed if not e["dedup_hit"]]
    assert len(real) == 1, "去重失败：同 command_id 同内容执行了 {} 次".format(len(real))
    # 同 ID 不同内容必须冲突；此时设备端应拒绝而不是再次运动
    await gw.send("device.command", command_payload(
        "command_1", "session_a", 1, future_ts(5), display_action("换了内容")))
    await deliver(client, await link.to_device.get())
    out = await gw.drain()
    results = [m for m in out if m["type"] == "device.result"]
    assert "IDEMPOTENCY_CONFLICT" in results[-1]["payload"]["detail"], results[-1]["payload"]["detail"]
    real = [e for e in client.executed if not e["dedup_hit"]]
    assert len(real) == 1, "冲突请求不得产生第二次执行，实际 {} 次".format(len(real))
    return "dedup 通过：同 ID 同内容只执行 1 次，同 ID 不同内容报 IDEMPOTENCY_CONFLICT 且不执行"


async def case_stale_lease():
    link = LoopbackLink()
    client = make_client(link)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    await gw.send("device.command", command_payload(
        "command_1", "session_a", 2, future_ts(5), display_action("安装 epoch 2")))
    await deliver(client, await link.to_device.get())
    assert client.lease.epoch == 2
    await gw.send("device.command", command_payload(
        "command_2", "session_a", 1, future_ts(5), display_action("旧 epoch")))
    await deliver(client, await link.to_device.get())
    out = await gw.drain()
    results = [m for m in out if m["type"] == "device.result"]
    assert "STALE_LEASE" in results[-1]["payload"]["detail"], results[-1]["payload"]["detail"]
    return "stale_lease 通过：lease_epoch 1 旧于当前 2，返回 STALE_LEASE"


async def case_expired():
    link = LoopbackLink()
    client = make_client(link)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    await gw.send("device.command", command_payload(
        "command_1", "session_a", 1, past_ts(1), display_action("已过期")))
    await deliver(client, await link.to_device.get())
    out = await gw.drain()
    results = [m for m in out if m["type"] == "device.result"]
    assert "EXPIRED" in results[-1]["payload"]["detail"], results[-1]["payload"]["detail"]
    real = [e for e in client.executed if not e["dedup_hit"]]
    assert not real, "过期动作不应执行"
    return "expired 通过：expires_at 已过，返回 EXPIRED 且未执行"


async def case_heartbeat_timeout():
    link = LoopbackLink()
    client = make_client(link, heartbeat_timeout=0.3)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    await gw.send("device.heartbeat", {"device_id": "passport_a"})
    await deliver(client, await link.to_device.get())
    stop_event = asyncio.Event()
    task = asyncio.ensure_future(client.run_heartbeat_loop(stop_event))
    # 之后不再发心跳；轮询等待停止发生，不用固定 sleep 赌时序
    for _ in range(60):
        if client.stop_fsm.stopping:
            break
        await asyncio.sleep(0.05)
    stop_event.set()
    await task
    assert client.stop_fsm.stopping, "心跳超时后应已进入停止"
    assert client.stop_fsm.pending == [], "心跳超时后待执行队列应被清空"
    assert HEARTBEAT_TIMEOUT_S == 3.0, "契约默认超时应为 3 秒"
    return "heartbeat_timeout 通过：超时即本地停止并清队列（默认阈值 3s）"


async def case_reconnect():
    link = LoopbackLink()
    client = make_client(link)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    # 让一个动作停在未确认状态
    client.unconfirmed["command_1"] = canonical_hash({"command_id": "command_1"})
    client.dedup.remember("command_1", client.unconfirmed["command_1"], "EXECUTING")
    before = len(client.executed)
    await client.reconnect()
    out = await gw.drain()
    results = [m for m in out if m["type"] == "device.result"]
    unknown = [r for r in results if r["payload"]["status"] == "UNKNOWN"]
    assert unknown, "重连后未确认命令必须报 UNKNOWN"
    assert len(client.executed) == before, "重连不得重放未确认命令"
    return "reconnect 通过：未确认命令报 UNKNOWN，无重放"


CASES = {
    "full_sequence": case_full_sequence,
    "dedup": case_dedup,
    "stale_lease": case_stale_lease,
    "expired": case_expired,
    "heartbeat_timeout": case_heartbeat_timeout,
    "reconnect": case_reconnect,
}


def build_parser():
    p = argparse.ArgumentParser(
        description="SUMMON 设备端参考实现：自检、模拟壳、以及连接真实 Gateway。")
    p.add_argument("--port", choices=("null", "cli"), default="null",
                   help="选择端口：null 打印到 stdout，cli 用键盘与终端")
    p.add_argument("--case", choices=tuple(sorted(CASES)) + ("all",), default="all",
                   help="运行哪个自检子用例，默认 all")
    p.add_argument("--selftest", action="store_true",
                   help="等价于 --case all，供 CI 调用")
    p.add_argument("--emit", metavar="PATH",
                   help="把一次完整收发的消息逐行写成 JSON，供校验器读取")
    p.add_argument("--connect", metavar="URL",
                   help="连接真实 Shell Gateway 的本地 WSS 地址，如 ws://127.0.0.1:8800/device")
    return p


async def run_cases(name: str) -> int:
    names = sorted(CASES) if name == "all" else [name]
    failed = []
    for n in names:
        try:
            message = await CASES[n]()
        except Exception as exc:                      # noqa: BLE001
            failed.append(n)
            print("FAIL {}: {}".format(n, exc))
        else:
            print("PASS {}: {}".format(n, message))
    if failed:
        print("FAIL: {} 个子用例未通过: {}".format(len(failed), ", ".join(failed)))
        return 1
    print("PASS: {} 个子用例全部通过".format(len(names)))
    return 0


async def emit_messages(path: str) -> int:
    """跑一次完整序列，把双向消息写成 JSON 行。"""
    link = LoopbackLink()
    client = make_client(link)
    gw = StubGateway(link, "passport_a")
    await client.hello()
    await gw.send("device.heartbeat", {"device_id": "passport_a"})
    await deliver(client, await link.to_device.get())
    await gw.send("device.command", command_payload(
        "command_1", "session_a", 1, future_ts(5), display_action("这是第一个展品。")))
    await deliver(client, await link.to_device.get())
    await gw.send("device.stop", {"session_id": "session_a", "lease_epoch": 1})
    await deliver(client, await link.to_device.get())
    out = await gw.drain()
    lines = []
    for msg in client.received + out:
        lines.append(json.dumps(msg, ensure_ascii=False))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("写出 {} 条消息到 {}".format(len(lines), path))
    return 0


async def connect_gateway(url: str, port_name: str) -> int:
    """连接真实 Gateway。需要 websockets：pip install -r protocol/requirements.txt"""
    try:
        import websockets                      # noqa: F401
    except ImportError:
        print("FAIL: 需要 websockets，请先运行 python -m pip install -r protocol/requirements.txt")
        return 2
    port = NullPort() if port_name == "null" else CliPort()
    client = DeviceClient("passport_a", "shell_a", port, verbose=True)
    print("连接 {} ...".format(url))
    async with websockets.connect(url) as ws:
        async def reader():
            async for raw in ws:
                await client.feed(raw if isinstance(raw, bytes) else raw.encode("utf-8"))

        async def writer():
            while True:
                if client.link is None:
                    await asyncio.sleep(0.05)
                    continue
                raw = await client.link.to_gateway.get()
                await ws.send(raw)

        client.link = LoopbackLink()
        stop_event = asyncio.Event()
        await client.hello()
        tasks = [asyncio.ensure_future(reader()),
                 asyncio.ensure_future(writer()),
                 asyncio.ensure_future(client.run_heartbeat_loop(stop_event))]
        await asyncio.gather(*tasks)
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.connect:
        return asyncio.run(
            connect_gateway(args.connect, args.port))
    if args.emit:
        return asyncio.run(emit_messages(args.emit))
    case = "all" if args.selftest else args.case
    return asyncio.run(run_cases(case))


if __name__ == "__main__":
    sys.exit(main())
