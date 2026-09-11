<img width="1280" height="640" alt="Useless Projects" src="https://github.com/user-attachments/assets/8920b256-2ba8-4988-b824-5351134eb4bd" />

# Tiny Neighborhood 🎯

## Basic Details

### Team Name: [Add team name]

### Team Members

- Team Lead: [Add name and college]
- Member 2: [Add name and college]
- Member 3: [Add name and college]

### Project Description

A tiny neighborhood that keeps living when nobody is watching. Citizens have their own limited knowledge and use the same validated actions that human players will eventually use.

### The Problem (that doesn't exist)

Real life is inconveniently large and difficult to observe all at once.

### The Solution (that nobody asked for)

Make a much smaller reality with one street, two homes, one shop, and three citizens whose lives can become unnecessarily dramatic.

## Technical Details

### Technologies/Components Used

- Python 3.10+
- Standard library only
- JSON persistence
- `unittest`

### Installation

No third-party packages are required.

### Run

```sh
python3 server.py
```

The headless server restores `data/world.json` and advances one simulated hour per real second. Stop it with Ctrl+C. Use `--save PATH` for another save or `--tick-seconds 0.5` for a faster clock.

### Verify

```sh
python3 -m unittest discover -s tests -v
```

## Current Systems

- A persistent world with one street, two homes, one shop, and three citizens.
- A simulation clock that runs without a browser connected.
- Atomic JSON saves, restoration, validation, and an ordered event log.
- Shared `talk` and connected-path `move` actions for human, rule-based, and AI callers.
- Private recipient memories, limited beliefs, and bounded relationship values.
- Bounded hunger and energy, a regular destination, a daily schedule, and visible activity.
- Deterministic citizens who travel to the shop, return home, rest, and sleep.
- Optional runtime-injected AI brains that may produce one validated reply to an incoming message.
- Persisted citizen traits, a personal goal, speaking style, and private per-pair reply cooldowns.

Public snapshots never expose private social state. Trusted server code can call `citizen_context(citizen_id)` for one citizen's detached private context.

AI providers implement the small `CitizenBrain.decide(request)` interface and are injected at runtime:

```python
world = World("data/world.json", ai_brains={"citizen-2": provider})
```

The request contains only that citizen's scoped context and allowed actions. Provider objects, credentials, model names, prompts, and timeout settings are not saved. With no injected brain, behavior is unchanged.

## Development Timeline

- [x] Phase 0 — Define the first version
- [x] Phase 1 — World foundation
- [x] Phase 2 — Shared social action, private memory, and relationships
- [x] Phase 3 — Everyday life and motives
- [x] Phase 4 — AI conversation and decisions
- [ ] Phase 5 — Deeper memory and relationships
- [ ] Phase 6 — Browser view
- [ ] Phase 7 — Human participation

Detailed checkpoints:

- [Phase 1 handoff](docs/phases/phase-1.md)
- [Phase 2 handoff](docs/phases/phase-2.md)
- [Phase 3 handoff](docs/phases/phase-3.md)
- [Phase 4 handoff](docs/phases/phase-4.md)

## Project Documentation

### Screenshots

Screenshots will be added when the browser view is implemented.

### Diagrams

The architecture diagram will be added with the browser and networking layers.

### Project Demo

The demo link will be added when the first playable version is ready.

## Team Contributions

- [Add contribution details]

---

Made with ❤️ at TinkerHub Useless Projects

![Static Badge](https://img.shields.io/badge/TinkerHub-24?color=%23000000&link=https%3A%2F%2Fwww.tinkerhub.org%2F)
![Static Badge](https://img.shields.io/badge/UselessProjects--26-26?link=https%3A%2F%2Ftinkerhub.org%2Fevents%2F1M8ORET9A1%2Fuseless-projects-3.0)
