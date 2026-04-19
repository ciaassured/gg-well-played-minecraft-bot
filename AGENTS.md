# AGENTS.md

## Project Overview

This repository is for a Minecraft reinforcement-learning bot trained to play
YRush, a Paper server minigame maintained in the GitHub repository:

```text
https://github.com/ciaassured/minecraft-yrush
```

YRush is a race to reach a target Y coordinate from a random safe start. The bot
project provides the client-side training interface and Python ML stack for that
game.

## Repository Shape

Expected layout:

```text
gg-well-played-minecraft-bot/
  client-mod/       Fabric client mod for observations and actions
  python/           PyTorch/RL code and debug controllers
  protocol/         Observation/action schemas and protocol notes
  AGENTS.md         This project guidance
```

## YRush Game Context

Relevant game facts for this bot:

- YRush V1 is a Paper server plugin.
- Target Y may be above or below the start.
- The public objective is direction plus distance, e.g. `CLIMB 42 BLOCKS` or
  `DIG DOWN 37 BLOCKS`.
- Water starts must use downward targets.
- Underground starts give a wooden pickaxe.
- Rounds wipe inventories intentionally.
- World changes remain between rounds.

## Target Architecture

Use this high-level architecture:

```text
Python RL/debug code
  <-> local socket/WebSocket JSON protocol
Fabric client mod
  <-> Minecraft client APIs
Paper server with YRush
```

Start with a normal GUI Minecraft client so behavior can be watched directly on
the developer machine. Headless training and Docker scaling come later.

## Client Mod Direction

Prefer Fabric for the client mod unless a future Minecraft version or tooling
constraint gives a strong reason to switch.

Current target:

- Minecraft `1.21.11`
- Fabric Loader
- Mojang/official mappings, not Yarn
- Java 21 toolchain
- Gradle wrapper should stay on the current stable Gradle line
- Use the latest stable Fabric/Loom packages that are compatible with
  Minecraft `1.21.11`; do not choose an incompatible newer Loom line just
  because it exists

The client mod should:

- run in a normal Minecraft GUI client first
- expose structured observations to Python
- accept action commands from Python
- apply movement, look, attack, use, jump, sneak, sprint, and hotbar selection
- provide a panic/release-all-keys command
- later support training/debug HUD information if useful

The mod is the Minecraft interface for ML. Keep it thin and pragmatic.

## Python Direction

Use Python and PyTorch for ML work.

The Python side should eventually contain:

- a debug controller for manual commands
- a Gymnasium-style environment wrapper
- reward calculation
- episode metrics
- model code
- training scripts
- checkpoint loading for GUI showcase runs

Do not start with RL. First prove Python can observe and control a GUI client.

## Protocol Direction

Use JSON over a local socket or WebSocket for the first implementation. JSON is
easy to inspect during early debugging. A binary protocol can be introduced only
after the shape is stable and performance demands it.

Initial observation should include:

- tick/time
- player position
- yaw and pitch
- velocity
- on-ground state
- water/lava/fire state where practical
- health, hunger, air
- selected hotbar slot
- inventory summary
- YRush objective state

Initial action should include:

- forward/back/left/right
- jump
- sneak
- sprint
- yaw/pitch delta or target look direction
- attack
- use
- selected hotbar slot
- release-all-keys

## YRush Objective Data

The bot should consume YRush objective data exposed by the game/server. If a
structured objective feed is not available yet, chat parsing is acceptable only
as an early prototype.

Useful round fields:

- round id
- active/inactive state
- start Y
- target Y
- direction
- total distance
- current distance remaining
- start type
- start category
- time remaining

## Nearby Block Observations

Do not start with vision. The project goal is structured numeric/symbolic state.

Start with a small local block area, not a giant cube. A reasonable first pass:

```text
x: -3..3
y: -2..3
z: -3..3
```

Useful block attributes:

- block id/category
- solid/passable
- liquid
- dangerous
- breakable
- estimated break time with current held item
- harvestable with current held item
- placeable/support surface
- climbable
- gravity affected

Also prefer derived movement features when they are easy to compute:

- block below is solid
- head is blocked
- front is blocked
- can jump forward
- can dig down
- standing on danger
- nearby liquid
- fall risk

## Skill System

The bot should not have to rediscover basic Minecraft recipes through random
trial and error. Support high-level skills as part of the action space.

Start with simple skills:

- craft planks
- craft sticks
- craft crafting table
- place crafting table
- craft wooden pickaxe
- equip best pickaxe
- mine looked-at block
- pillar up once
- dig down once
- release all keys

Later skills may include:

- dig staircase down
- pillar toward target
- collect nearby tree
- craft best available pickaxe
- escape water
- recover from stuck state

The policy can learn when to use skills while skills handle known mechanical
procedures.

## Reward Direction

Keep first rewards simple:

```text
+ progress toward target Y
+ win bonus
- death penalty
- small time penalty
- stuck/no-progress penalty
```

Inventory/resource rewards should be small and milestone-based. Avoid teaching
the model to mine forever instead of winning.

## Episode Data And Replay

Structured episode data is for training metrics. Replay Mod is for video
content, especially YouTube footage.

Record episode metrics such as:

- result
- duration
- start/target/direction
- best distance remaining
- final distance remaining
- total reward
- death/damage info
- resources collected
- skills used
- checkpoint id

Replay Mod is not required for the first training loop. Add it later for GUI
showcase runs or selected best episodes.

## Milestones

Recommended order:

1. Scaffold Fabric client mod and Python debug controller.
2. Launch GUI Minecraft with the mod and connect Python.
3. Print live observations from the GUI client.
4. Send movement/look/attack/use actions from Python.
5. Add panic/release-all-keys.
6. Detect YRush objective, initially via chat if needed.
7. Consume structured YRush round data once the server exposes it.
8. Add local block observations and movement features.
9. Add simple skills.
10. Wrap Python side as a Gymnasium-style environment.
11. Build a scripted baseline policy.
12. Start PyTorch RL training.
13. Move to headless clients and Docker scaling.
14. Add Replay Mod/OBS workflow for selected video runs.

## Development Rules

- Prefer small, testable milestones over building the whole RL system at once.
- Keep protocol files readable and versioned.
- Keep action execution deterministic where practical.
- Always include a release-all-keys path during action/control work.
- Preserve unrelated user changes.
- Avoid broad refactors unless they directly support the current milestone.
