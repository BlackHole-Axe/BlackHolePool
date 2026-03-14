# SoloPool — Solo Bitcoin Mining Pool

A production-ready solo Bitcoin mining pool written in Rust + React.
Connects directly to your own Bitcoin Core node via Docker Compose.

---

## Quick Start (5 steps)

```bash
1.  Edit  env/.env  — fill in the 6 required fields (see Section 1 below)
2.  docker compose up -d --build
3.  Point miners at:  stratum+tcp://POOL_HOST_IP:2018
4.  Open Dashboard:   http://POOL_HOST_IP:3334
5.  Health check:     curl http://POOL_HOST_IP:8081/health  → {"ok":true}
```

---

## Requirements

| | Version |
|---|---|
| Docker | 20.10+ |
| Docker Compose | v2+ |
| Bitcoin Core | 26.0+ |
| RAM | 512 MB |
| OS | Linux / macOS / Windows (WSL2) |

**Umbrel users:** The pool connects to Bitcoin Core over the internal
`umbrel_main_network` Docker network — no extra network configuration needed.

---

## Section 1 — Configure env/.env

Open `env/.env` and fill in these 6 fields:

### 1. RPC_URL

| Situation | Value |
|---|---|
| Umbrel (inside Docker) | `http://10.21.21.8:8332` |
| Same machine, outside Docker | `http://127.0.0.1:8332` |
| Different machine on LAN | `http://192.168.1.X:8332` |

### 2. RPC_USER

Usually `umbrel` on Umbrel. Check `bitcoin.conf` for `rpcuser=`.

### 3. RPC_PASS

**On Umbrel:**
```bash
cat ~/umbrel/app-data/bitcoin/.env
# Output:  export APP_BITCOIN_RPC_PASS='YOUR_PASSWORD_HERE'
```

**Standard node:**
```bash
grep rpcpassword ~/.bitcoin/bitcoin.conf
```

### 4 & 5. ZMQ_BLOCKS and ZMQ_TXS

Same IP as RPC_URL. Example for Umbrel:
```ini
ZMQ_BLOCKS=tcp://10.21.21.8:28334,tcp://10.21.21.8:28332
ZMQ_TXS=tcp://10.21.21.8:28336,tcp://10.21.21.8:28333
```

### 6. PAYOUT_ADDRESS

Your Bitcoin address. Block rewards go here when miners don't specify their own.

---

## Section 2 — Bitcoin Core Configuration

Add these 4 lines to `bitcoin.conf` (or `umbrel-bitcoin.conf` on Umbrel),
then **restart Bitcoin Core**:

```ini
zmqpubhashblock=tcp://0.0.0.0:28334
zmqpubrawblock=tcp://0.0.0.0:28332
zmqpubhashtx=tcp://0.0.0.0:28336
zmqpubrawtx=tcp://0.0.0.0:28333
```

**On Umbrel**, the file is at:
```
~/umbrel/app-data/bitcoin/data/bitcoin/umbrel-bitcoin.conf
```

> Without ZMQ, the pool polls every 30s. With ZMQ, new blocks are detected
> in < 100ms — dramatically reducing stale shares.

---

## Section 3 — Start the Pool

```bash
cd SoloPool
docker compose up -d --build
```

First run compiles Rust (~3–5 min). Subsequent starts are instant.

**Verify:**
```bash
docker compose logs pool --tail=20
curl http://localhost:8081/health    # {"ok":true}
curl http://localhost:8081/network   # shows block height + difficulty
```

**Ports:**

| Port | Purpose |
|---|---|
| **2018** | Stratum v1 — miners connect here |
| **3334** | Web Dashboard |
| **8081** | REST API (read-only) |

---

## Section 4 — Configure Your Miners

```
Protocol:   Stratum v1
URL:        stratum+tcp://POOL_HOST_IP:2018
Username:   bc1qYOUR_BITCOIN_ADDRESS
Password:   x
```

The pool recognises the address in Username and pays rewards directly to it.

**Multiple workers from same address:**
```
Miner 1 → Username: bc1qXXXX.Rig1
Miner 2 → Username: bc1qXXXX.Rig2
```

---

## Section 5 — Dashboard and API

```bash
# Dashboard
http://POOL_HOST_IP:3334

# API endpoints
curl http://POOL_HOST_IP:8081/pool      # hashrate, stale ratio, ZMQ counters
curl http://POOL_HOST_IP:8081/miners    # per-miner stats + best difficulty
curl http://POOL_HOST_IP:8081/blocks    # blocks found
curl http://POOL_HOST_IP:8081/network   # network difficulty + hashrate
```

---

## Section 6 — Vardiff Tuning

| Miner | Hashrate | MIN_DIFFICULTY |
|---|---|---|
| Bitaxe / NerdAxe | 200–500 GH/s | 512 |
| NerdQAxe++ | 1–8 TH/s | 8192 |
| Small ASIC | 10–50 TH/s | 65536 |
| Large ASIC | 100+ TH/s | 524288 |

---

## Section 7 — Useful Commands

```bash
docker compose restart pool          # restart pool only
docker compose logs -f pool          # watch live logs
docker compose down                  # stop everything
docker compose up -d --build         # rebuild + restart
bash reset-pool.sh                   # wipe DB and restart fresh
```

---

## Section 8 — Troubleshooting

**Pool cannot connect to Bitcoin Core:**
```bash
curl --user RPC_USER:RPC_PASS -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"1.0","method":"getblockchaininfo","params":[]}' \
  http://BITCOIN_NODE_IP:8332/
```

**ZMQ not connecting:**
```bash
docker compose logs pool | grep ZMQ
# Expected:
# ZMQ block connected: tcp://...28334
# ZMQ block connected: tcp://...28332
```

**"Low difficulty share" from miners:**
Lower `MIN_DIFFICULTY` in `env/.env` then `docker compose restart pool`.

**Port 2018 not reachable:**
```bash
sudo ufw allow 2018/tcp
sudo ufw allow 3334/tcp
```

---

## Section 9 — Architecture

```
Bitcoin Core
  ↕ ZMQ hashblock+rawblock (dual endpoints, < 100ms detection)
  ↕ HTTP Longpoll (fallback, ~90s hold)
  ↕ submitblock (5 retries + chain verification)
SoloPool (Rust + Tokio async)
  ↕ Stratum v1 TCP :2018
Miners (Bitaxe / NerdQAxe++ / cgminer)
```

**Security hardening included:**
- Max Stratum line length: 64 KB (prevents OOM DOS)
- Idle timeout: 300s (reaps dead connections)
- Malformed input returns error response (connection not closed)
- Stale grace window: 300ms (eliminates NewBlock stale shares)
- ZMQ dual-endpoint redundancy (block detection survives partial failure)
- 5-retry submitblock + chain verification (block cannot be lost)

---

## Section 10 — File Structure

```
SoloPool/
├── docker-compose.yml          ← Entry point
├── reset-pool.sh               ← Wipe DB and restart
├── LICENSE
├── env/
│   └── .env                    ← ★ Fill in your values here ★
├── pool/                       ← Rust backend
│   ├── Dockerfile
│   ├── Cargo.toml
│   ├── tests/
│   │   ├── stratum_protocol.rs ← 14 protocol robustness tests
│   │   └── block_safety.rs     ← 5 block-loss scenario proofs
│   └── src/
│       ├── main.rs
│       ├── config.rs
│       ├── stratum/mod.rs      ← Stratum v1 server (hardened)
│       ├── template/mod.rs     ← GBT + ZMQ + block builder (BIP34 fixed)
│       ├── share/mod.rs        ← SHA256d validation (39 unit tests)
│       ├── rpc.rs              ← Bitcoin Core RPC client
│       └── vardiff.rs
└── dashboard/                  ← React frontend
    └── src/
```

---

## Security Notes

- No secrets hard-coded — all from `env/.env`
- `AUTH_TOKEN` empty = open access (fine for home LAN)
- If exposing port 2018 to the internet, set a strong `AUTH_TOKEN`
- REST API and Dashboard are read-only
- RPC password never logged

---

## License

MIT — see `LICENSE`
