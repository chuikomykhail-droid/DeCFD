# DeCFD: decentralized evolutionary aerodynamics

A genetic algorithm designs wing profiles. A network of miners runs the wind-tunnel
simulations. Miners who fake results get caught and lose their stake.

![The best shape of each generation and the flow around it](docs/demo/data/run/report/evolution.gif)

**[Open the demo dashboard](https://chuikomykhail-droid.github.io/DeCFD/demo/)** — a full
run replayed: evolution, miners, stakes and every ledger event.

## Why

Design optimization by simulation needs thousands of independent CFD runs: a natural
workload for a DePIN / ComputeFi network. The hard part is trusting results computed by
strangers. DeCFD makes a fake result cost more than it earns:

- **Deterministic solver.** The same binary and inputs give bit-identical output, so any
  result can be re-checked exactly.
- **Commit, then audit.** Miners submit a hash of each result; a verifier re-computes a
  random 20% plus every result the optimizer is about to build on.
- **Stake and slashing.** A mismatch costs the miner half its stake (half of that goes to
  the verifier); a second catch bans it. The client's budget sits in escrow and pays only
  for tasks that survive.

## Status: v1

| Layer | State |
|---|---|
| Physics worker: C++ lattice Boltzmann (D2Q9), validated on the cylinder benchmark | ✅ |
| Optimizer: genetic algorithm with adaptive mutation | ✅ |
| Network: jobs, tasks, signed submissions, audits, slashing, payouts | ✅ runs locally |
| Ledger: accounts and instructions of the Solana program | ✅ in-memory mock behind a `Ledger` interface |
| Dashboard (live and replay), run report, tests | ✅ |
| Solana devnet program | next: plugs in behind the same interface |

## Demo run

`python orchestrator_py/app.py --pop 12 --gen 20 --quiet-net`: seed 42, four miners (one of
them a simulated cheater), 10 minutes on a 12-thread laptop.

| | |
|---|---|
| Best lift / drag | 0.80 in generation 0 → **2.27** in generation 16 (+184%) |
| Same shape re-run with 30 000 steps | 2.28 (+0.6%) |
| Tasks / audits | 221 / 61 (28% re-computed) |
| Fraud | 3 fakes, all caught; the cheater was banned in epoch 2 |
| Job budget 2400 | 2180 paid for honest work, 220 refunded to the client |
| Slashed stake 87 | 43 to the verifier, 44 to the treasury |

![Best shape at five generations, overlaid](docs/demo/data/run/report/shapes.png)

![Flow around the generation-0 winner and the final winner](docs/demo/data/run/report/before_after.png)

![Tasks and audits per epoch; earnings and stakes per miner](docs/demo/data/run/report/network.png)

The final shape is a thin, cambered plate at 13°; the thick random shapes of generation 0
reach at most 0.80. Its wake is steady; the animation below shows the flow starting from rest,
with the starting vortex leaving the trailing edge.

![The flow starting around the final shape](docs/demo/data/run/report/startup.gif)


## Is the solver right?

Before trusting the optimizer, the solver is checked on the textbook benchmark: flow past a
circular cylinder, compared with published values ([full report](docs/validation/cylinder.md)).

| Benchmark | DeCFD | Published range |
|---|---|---|
| Re = 20: drag coefficient Cd | 2.15 | 2.00–2.22 |
| Re = 20: wake length Lr/D | 0.95 | 0.91–0.94 |
| Re = 40: drag coefficient Cd | 1.60 | 1.48–1.62 |
| Re = 40: wake length Lr/D | 2.33 | 2.13–2.35 |
| Re = 100: shedding frequency (Strouhal) | 0.164 | 0.160–0.175 |
| Re = 100: mean drag Cd | 1.42 | 1.33–1.38 |
| Re = 100: lift amplitude | 0.38 | 0.25–0.34 |

The steady wakes and the shedding frequency match. Drag and lift amplitude at Re = 100 are
3% and 11% high, as expected from the 5% blockage of the simulated channel (the references
are for an unbounded cylinder).

![Von Kármán vortex street behind a cylinder at Re = 100](docs/validation/vortex_street.gif)

## Quick start (Windows)

Requirements: Python 3.9+ and Visual Studio 2019+ (or Build Tools) with the
*Desktop development with C++* workload.

```powershell
pip install -r orchestrator_py/requirements.txt
powershell -ExecutionPolicy Bypass -File build.ps1          # -> worker_cpp\worker.exe
python orchestrator_py/serve.py                             # dashboard at http://localhost:8000
python orchestrator_py/app.py --pop 12 --gen 20 --quiet-net # ≈ 12 min on a 12-thread laptop
```

Open the dashboard while the run is going: it updates every two seconds. The report
(`report/` in the run folder) is built automatically at the end.

| Command | What it does |
|---|---|
| `python orchestrator_py/app.py` | Smoke test: 4 shapes × 3 generations, about a minute |
| `python orchestrator_py/report.py [run]` | Rebuild a run's report |
| `python orchestrator_py/export_demo.py [run]` | Export the dashboard + a run to `docs/demo/` (GitHub Pages) |
| `python -m unittest discover -s tests` | Ledger rules, worker determinism, fraud detection (≈ 15 s) |
| `python validation/cylinder.py` | Cylinder benchmark (≈ 45 min; needs the bigger-grid builds, see the script) |

**CMake** (any platform; also used by the VS Code CMake Tools extension):

```bash
cmake -S worker_cpp -B worker_cpp/build/cmake -DCMAKE_BUILD_TYPE=Release
cmake --build worker_cpp/build/cmake --config Release      # -> worker_cpp/worker[.exe]
```

The CMake build is tested with MSVC. GCC/Clang with OpenMP should work but has not been
tested yet.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--pop` | 4 | shapes per generation |
| `--gen` | 3 | generations (= network epochs) |
| `--miners` | 4 | mock miner nodes |
| `--cheaters` | 1 | how many of them are lazy |
| `--cheat-prob` | 0.5 | chance a lazy miner fakes a task |
| `--verify-rate` | 0.2 | fraction of tasks audited at random |
| `--parent-audit` | off | also audit every unverified tournament winner before it reproduces |
| `--steps`, `--avg` | 6000, 2000 | solver steps per evaluation and steps averaged (job-wide) |
| `--ledger` | mock | chain backend; the Solana devnet backend plugs in here |
| `--seed` | 42 | GA seed; runs are reproducible |
| `--quiet-net` | off | hide per-task log lines (fraud events are still shown) |

## How it works

Each generation of the GA is one network epoch:

1. The client opens a **job** once per run and escrows its budget. The job pins the hash of `worker.exe`.
2. Every new shape becomes a **task** that reserves one reward and pins the hash of its parameters.
3. Miners compute in parallel and **submit a hash** of their result.
4. The verifier re-computes a random 20% of tasks plus the generation leader before it becomes the elite.
5. A mismatch **slashes** the miner and triggers a re-audit of its other work in this epoch.
6. Every surviving task is **settled** (paid). At the end the unspent budget returns to the client.

All chain access goes through one interface, [`network/ledger.py`](orchestrator_py/network/ledger.py),
which documents every account and instruction of the planned Solana program. Details,
economics and the event-log format are in [architecture.md](architecture.md).

## Honest caveats

- **Physics.** 2D, laminar, Reynolds number ≈ 180: the regime of insects and micro-drones, not of aircraft. Shapes are "optimal for this model", not real wings.
- **Objective.** Maximizing lift/drag in 2D ignores induced drag. The network does not care what the fitness function is, so a client could ask for minimum drag at a fixed lift instead.
- **Network.** Miners are threads of one process, the ledger is an in-memory mock, and there is a single trusted verifier. Bit-exact verification assumes every miner runs the same binary.

## Repository layout

```
build.ps1                    Windows build script (MSVC)
worker_cpp/main.cpp          LBM solver: shape parameters in, drag/lift JSON out
orchestrator_py/app.py       genetic algorithm (the network's client)
orchestrator_py/network/     Ledger interface, mock Solana program, wallets, miners, ComputeNetwork
orchestrator_py/report.py    run report; serve.py / export_demo.py for the dashboard
dashboard/                   web dashboard (HTML/JS, no build step)
validation/cylinder.py       solver validation
tests/                       unit and end-to-end tests
docs/                        validation report, exported demo
architecture.md              design, protocol, economics, validation, limitations
```

## License

[MIT](LICENSE)
