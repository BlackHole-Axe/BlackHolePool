#!/usr/bin/env python3
"""
BlackHole regtest end-to-end test.

Covers:
- bitcoind regtest RPC/ZMQ startup
- pool template refresh + notify
- miner subscribe/authorize/submit
- share acceptance
- duplicate rejection
- best-share update visibility
- block candidate discovery + submitblock acceptance
- clean_jobs notify + stale-old-job rejection
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


NETWORK = "blackhole-regtest-net"
BITCOIN_CONTAINER = "blackhole-regtest-bitcoind"
POOL_CONTAINER = "blackhole-regtest-pool"
POOL_IMAGE = "blackhole-regtest-pool:latest"
BITCOIN_IMAGE = "ruimarinho/bitcoin-core:24"

RPC_USER = "regtest"
RPC_PASS = "regtestpass"
RPC_PORT = 18443
STRATUM_PORT = 22018
API_PORT = 28081
WORKER = "e2e.worker1"

KEEP_REGTEST = os.environ.get("KEEP_REGTEST", "").lower() in {"1", "true", "yes"}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd, check=True, capture=False):
    print("+", " ".join(cmd))
    result = subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def docker(*args, check=True, capture=False):
    return run(["sudo", "-n", "docker", *args], check=check, capture=capture)


def docker_rm(name: str):
    docker("rm", "-f", name, check=False)


def cleanup():
    docker_rm(POOL_CONTAINER)
    docker_rm(BITCOIN_CONTAINER)
    docker("network", "rm", NETWORK, check=False)


def http_json(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def rpc_call(method: str, params=None, wallet: str | None = None):
    if params is None:
        params = []
    payload = json.dumps(
        {"jsonrpc": "1.0", "id": "e2e", "method": method, "params": params}
    ).encode("utf-8")
    if wallet:
        url = f"http://127.0.0.1:{RPC_PORT}/wallet/{wallet}"
    else:
        url = f"http://127.0.0.1:{RPC_PORT}"
    request = urllib.request.Request(url, data=payload)
    request.add_header("Content-Type", "application/json")
    auth = base64.b64encode(f"{RPC_USER}:{RPC_PASS}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(request, timeout=8) as response:
        body = json.loads(response.read().decode("utf-8"))
    if body.get("error") is not None:
        raise RuntimeError(f"RPC error for {method}: {body['error']}")
    return body["result"]


def wait_until(predicate, timeout_s: float, message: str):
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as err:  # pragma: no cover - debug path
            last_err = err
        time.sleep(0.5)
    if last_err:
        raise RuntimeError(f"{message}. Last error: {last_err}")
    raise RuntimeError(message)


class StratumClient:
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=8)
        self.sock.settimeout(1.0)
        self.buffer = b""
        self.next_id = 100

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def send(self, payload: dict):
        wire = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        self.sock.sendall(wire)

    def recv_message(self, timeout_s: float = 10.0) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                return json.loads(line.decode("utf-8"))
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise RuntimeError("stratum socket closed")
                self.buffer += chunk
            except socket.timeout:
                continue
        raise TimeoutError("Timed out waiting for stratum message")

    def recv_until(self, predicate, timeout_s: float = 15.0) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            msg = self.recv_message(timeout_s=max(0.1, deadline - time.time()))
            if predicate(msg):
                return msg
        raise TimeoutError("Timed out waiting for expected stratum message")

    def submit(self, worker: str, job_id: str, extranonce2: str, ntime: str, nonce: str):
        req_id = self.next_id
        self.next_id += 1
        self.send(
            {
                "id": req_id,
                "method": "mining.submit",
                "params": [worker, job_id, extranonce2, ntime, nonce],
            }
        )
        return self.recv_until(lambda m: m.get("id") == req_id, timeout_s=10.0)


def start_bitcoind():
    docker(
        "run",
        "-d",
        "--rm",
        "--name",
        BITCOIN_CONTAINER,
        "--network",
        NETWORK,
        "-p",
        f"{RPC_PORT}:18443",
        "-p",
        "28332:28332",
        "-p",
        "28333:28333",
        "-p",
        "28334:28334",
        "-p",
        "28336:28336",
        BITCOIN_IMAGE,
        "bitcoind",
        "-regtest=1",
        "-server=1",
        "-rpcbind=0.0.0.0",
        "-rpcallowip=0.0.0.0/0",
        f"-rpcuser={RPC_USER}",
        f"-rpcpassword={RPC_PASS}",
        "-rpcport=18443",
        "-txindex=1",
        "-fallbackfee=0.0002",
        "-zmqpubhashblock=tcp://0.0.0.0:28334",
        "-zmqpubrawblock=tcp://0.0.0.0:28332",
        "-zmqpubhashtx=tcp://0.0.0.0:28336",
        "-zmqpubrawtx=tcp://0.0.0.0:28333",
        "-printtoconsole=1",
    )

    wait_until(
        lambda: rpc_call("getblockchaininfo"),
        timeout_s=60,
        message="bitcoind RPC not ready",
    )
    print("bitcoind RPC ready")

    # wallet init + mining funds
    try:
        rpc_call("createwallet", ["e2e"], wallet=None)
    except Exception:
        # wallet may already exist if container re-used unexpectedly
        pass
    payout = rpc_call("getnewaddress", [], wallet="e2e")
    rpc_call("generatetoaddress", [120, payout], wallet="e2e")
    return payout


def build_pool_image():
    git_sha = run(["git", "rev-parse", "--verify", "HEAD"], capture=True)
    git_dirty = "true" if run(["git", "status", "--porcelain"], capture=True) else "false"
    build_time = now_utc()

    docker(
        "build",
        "-t",
        POOL_IMAGE,
        "--build-arg",
        f"BUILD_GIT_SHA={git_sha}",
        "--build-arg",
        f"BUILD_GIT_DIRTY={git_dirty}",
        "--build-arg",
        "BUILD_SOURCE=regtest-e2e",
        "--build-arg",
        f"BUILD_TIME_UTC={build_time}",
        "--build-arg",
        "BUILD_IMAGE_ID=regtest-e2e",
        "./pool",
    )
    return docker("image", "inspect", POOL_IMAGE, "--format", "{{.Id}}", capture=True)


def start_pool(payout_address: str, image_id: str):
    docker(
        "run",
        "-d",
        "--rm",
        "--name",
        POOL_CONTAINER,
        "--network",
        NETWORK,
        "-p",
        f"{STRATUM_PORT}:2018",
        "-p",
        f"{API_PORT}:8080",
        "-e",
        "BITCOIN_NETWORK=regtest",
        "-e",
        "RPC_URL=http://blackhole-regtest-bitcoind:18443",
        "-e",
        f"RPC_USER={RPC_USER}",
        "-e",
        f"RPC_PASS={RPC_PASS}",
        "-e",
        "ZMQ_BLOCKS=tcp://blackhole-regtest-bitcoind:28334,tcp://blackhole-regtest-bitcoind:28332",
        "-e",
        "ZMQ_TXS=tcp://blackhole-regtest-bitcoind:28336,tcp://blackhole-regtest-bitcoind:28333",
        "-e",
        f"PAYOUT_ADDRESS={payout_address}",
        "-e",
        "STRATUM_BIND=0.0.0.0",
        "-e",
        "STRATUM_PORT=2018",
        "-e",
        "API_BIND=0.0.0.0",
        "-e",
        "API_PORT=8080",
        "-e",
        "PERSIST_SHARES=false",
        "-e",
        "PERSIST_BLOCKS=true",
        "-e",
        "VARDIFF_ENABLED=false",
        "-e",
        "STRATUM_START_DIFFICULTY=0.000000000001",
        "-e",
        "MIN_DIFFICULTY=0.000000000001",
        "-e",
        "MAX_DIFFICULTY=1024",
        "-e",
        "TARGET_SHARE_TIME_SECS=10",
        "-e",
        "VARDIFF_RETARGET_SECS=30",
        "-e",
        "TEMPLATE_POLL_MS=1000",
        "-e",
        "JOB_REFRESH_MS=1000",
        "-e",
        "ZMQ_DEBOUNCE_MS=50",
        "-e",
        "POST_BLOCK_SUPPRESS_MS=500",
        "-e",
        "NOTIFY_BUCKET_CAPACITY=10",
        "-e",
        "NOTIFY_BUCKET_REFILL_MS=10",
        "-e",
        f"RUNTIME_IMAGE_REF={POOL_IMAGE}",
        "-e",
        f"RUNTIME_IMAGE_ID={image_id}",
        "-e",
        f"RUNTIME_CONTAINER_NAME={POOL_CONTAINER}",
        POOL_IMAGE,
    )

    wait_until(
        lambda: http_json(f"http://127.0.0.1:{API_PORT}/health"),
        timeout_s=60,
        message="pool API /health not ready",
    )
    wait_until(
        lambda: http_json(f"http://127.0.0.1:{API_PORT}/pool"),
        timeout_s=30,
        message="pool API /pool not ready",
    )


def require(condition: bool, message: str):
    if not condition:
        raise RuntimeError(message)


def error_code(resp: dict):
    err = resp.get("error")
    if isinstance(err, list) and err:
        return err[0]
    return None


def main():
    client = None
    try:
        cleanup()
        docker("network", "create", NETWORK)

        payout = start_bitcoind()
        image_id = build_pool_image()
        start_pool(payout, image_id)

        build_info = http_json(f"http://127.0.0.1:{API_PORT}/build-info")
        require(build_info.get("git_sha") not in (None, "", "unknown"), "build-info git_sha missing")
        require(build_info.get("runtime_image_id") not in (None, "", "unknown"), "build-info runtime_image_id missing")

        pool_before = http_json(f"http://127.0.0.1:{API_PORT}/pool")
        chain_before = rpc_call("getblockcount")

        client = StratumClient("127.0.0.1", STRATUM_PORT)
        client.send({"id": 1, "method": "mining.subscribe", "params": ["regtest-e2e/1.0"]})
        sub = client.recv_until(lambda m: m.get("id") == 1)
        require(sub.get("error") is None, f"subscribe failed: {sub}")
        extranonce1 = sub["result"][1]
        extranonce2_size = int(sub["result"][2])
        require(extranonce2_size > 0, "invalid extranonce2_size")
        print(f"subscribed extranonce1={extranonce1} extranonce2_size={extranonce2_size}")

        client.send({"id": 2, "method": "mining.authorize", "params": [WORKER, "x"]})
        auth = client.recv_until(lambda m: m.get("id") == 2)
        require(auth.get("result") is True, f"authorize failed: {auth}")

        notify = client.recv_until(lambda m: m.get("method") == "mining.notify", timeout_s=25)
        params = notify["params"]
        current_job_id = params[0]
        current_ntime = params[7]
        print(f"first notify job_id={current_job_id} clean_jobs={params[8]}")

        extranonce2 = "1".rjust(extranonce2_size * 2, "0")
        accepted_nonce = None
        accepted_job_id = None
        accepted_ntime = None

        for i in range(200):
            nonce = f"{i:08x}"
            resp = client.submit(WORKER, current_job_id, extranonce2, current_ntime, nonce)
            if resp.get("result") is True:
                accepted_nonce = nonce
                accepted_job_id = current_job_id
                accepted_ntime = current_ntime
                print(f"accepted share nonce={nonce}")
                break
            if error_code(resp) == 21:
                notify = client.recv_until(lambda m: m.get("method") == "mining.notify", timeout_s=20)
                current_job_id = notify["params"][0]
                current_ntime = notify["params"][7]
                continue
        require(accepted_nonce is not None, "failed to submit an accepted share")

        dup = client.submit(WORKER, accepted_job_id, extranonce2, accepted_ntime, accepted_nonce)
        require(dup.get("result") is False and error_code(dup) == 22, f"duplicate rejection failed: {dup}")

        miners = http_json(f"http://127.0.0.1:{API_PORT}/miners")
        worker = next((m for m in miners if m["worker"] == WORKER), None)
        require(worker is not None, "worker not visible in /miners")
        require(worker["shares"] >= 1, "worker shares not incremented")
        require(worker["best_difficulty"] > 0.0, "best_difficulty not updated")

        # Force one external block first, then old job must become stale.
        old_job_id_for_stale = current_job_id
        old_ntime_for_stale = current_ntime
        external_height_before = rpc_call("getblockcount")
        rpc_call("generatetoaddress", [1, payout], wallet="e2e")
        wait_until(
            lambda: rpc_call("getblockcount") >= external_height_before + 1,
            timeout_s=20,
            message="regtest did not mine external block",
        )
        clean_notify = client.recv_until(
            lambda m: m.get("method") == "mining.notify"
            and bool(m.get("params", [None] * 9)[8])
            and m.get("params", [None])[0] != old_job_id_for_stale,
            timeout_s=30,
        )
        current_job_id = clean_notify["params"][0]
        current_ntime = clean_notify["params"][7]
        print(f"clean notify received job_id={current_job_id} after external block")

        # Stale-block policy in this pool is "accept for metrics, never submit as block".
        # Probe multiple old-job shares (very high chance at least one is network-target valid
        # on regtest) and verify chain height + submitblock counters do NOT move.
        stale_probe_before_height = rpc_call("getblockcount")
        pool_before_stale_probe = http_json(f"http://127.0.0.1:{API_PORT}/pool")
        stale_probe_accepts = 0
        for i in range(40):
            nonce = f"{0x90000000 + i:08x}"
            resp = client.submit(WORKER, old_job_id_for_stale, extranonce2, old_ntime_for_stale, nonce)
            if resp.get("result") is True:
                stale_probe_accepts += 1
        stale_probe_after_height = rpc_call("getblockcount")
        pool_after_stale_probe = http_json(f"http://127.0.0.1:{API_PORT}/pool")
        require(stale_probe_accepts >= 1, "stale probe did not accept any old-job shares")
        require(
            stale_probe_after_height == stale_probe_before_height,
            "stale-block shares unexpectedly changed chain height",
        )
        require(
            pool_after_stale_probe["submitblockAccepted"] == pool_before_stale_probe["submitblockAccepted"],
            "stale-block shares unexpectedly triggered submitblock",
        )

        # Find at least one block candidate by trying multiple nonces.
        # On regtest (0x207fffff) this is usually quick.
        block_found = False
        chain_before_candidate = rpc_call("getblockcount")
        for i in range(1000, 5000):
            nonce = f"{i:08x}"
            resp = client.submit(WORKER, current_job_id, extranonce2, current_ntime, nonce)
            code = error_code(resp)
            if code == 21:
                notify = client.recv_until(lambda m: m.get("method") == "mining.notify", timeout_s=20)
                current_job_id = notify["params"][0]
                current_ntime = notify["params"][7]
                continue
            if resp.get("result") is not True:
                continue
            height_now = rpc_call("getblockcount")
            if height_now > chain_before_candidate:
                block_found = True
                print(f"block accepted in chain at height={height_now}")
                break
        require(block_found, "failed to find a regtest block candidate via mining.submit")

        pool_after = http_json(f"http://127.0.0.1:{API_PORT}/pool")
        require(pool_after["duplicateShares"] >= pool_before["duplicateShares"] + 1, "duplicateShares counter did not increase")
        require(pool_after["cleanJobsSent"] >= pool_before["cleanJobsSent"] + 1, "cleanJobsSent did not increase")
        require(
            pool_after["submitblockAccepted"] >= pool_after_stale_probe["submitblockAccepted"] + 1,
            "submitblockAccepted did not increment on fresh job",
        )

        summary = {
            "result": "ok",
            "chain_before": chain_before,
            "chain_after": rpc_call("getblockcount"),
            "pool_before": {
                "cleanJobsSent": pool_before["cleanJobsSent"],
                "duplicateShares": pool_before["duplicateShares"],
                "submitblockAccepted": pool_before["submitblockAccepted"],
                "stalesNewBlock": pool_before["stalesNewBlock"],
            },
            "pool_after": {
                "cleanJobsSent": pool_after["cleanJobsSent"],
                "duplicateShares": pool_after["duplicateShares"],
                "submitblockAccepted": pool_after["submitblockAccepted"],
                "stalesNewBlock": pool_after["stalesNewBlock"],
            },
            "stale_probe": {
                "accepted_shares": stale_probe_accepts,
                "chain_height_before": stale_probe_before_height,
                "chain_height_after": stale_probe_after_height,
                "submitblock_before": pool_before_stale_probe["submitblockAccepted"],
                "submitblock_after": pool_after_stale_probe["submitblockAccepted"],
            },
            "build_info": build_info,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
    finally:
        if client is not None:
            client.close()
        if not KEEP_REGTEST:
            cleanup()
        else:
            print("KEEP_REGTEST enabled: containers/network left running")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"regtest e2e failed: {exc}", file=sys.stderr)
        try:
            docker("logs", "--tail", "200", POOL_CONTAINER, check=False)
            docker("logs", "--tail", "200", BITCOIN_CONTAINER, check=False)
        except Exception:
            pass
        sys.exit(1)
