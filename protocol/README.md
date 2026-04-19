# YRush Bot Bridge Protocol

The first bridge protocol is newline-delimited JSON over TCP.

```text
host: 127.0.0.1
port: 47321
encoding: UTF-8
framing: one JSON object per line
```

## Hello

Sent by the mod after a Python client connects:

```json
{"type":"hello","protocol_version":1,"mod":"yrush-bot-client","minecraft_version":"1.21.11"}
```

## Observation

Sent by the mod every few client ticks:

```json
{
  "type": "observation",
  "protocol_version": 1,
  "tick": 12345,
  "screen": {
    "open": false
  },
  "player": {
    "present": true,
    "x": 100.25,
    "y": 64.0,
    "z": -40.5,
    "yaw": 90.0,
    "pitch": 12.0,
    "vx": 0.0,
    "vy": -0.08,
    "vz": 0.1,
    "on_ground": true,
    "in_water": false,
    "in_lava": false,
    "health": 20.0,
    "hunger": 20,
    "air": 300
  },
  "inventory": {
    "present": true,
    "selected_slot": 0,
    "hotbar": [
      {"slot": 0, "item": "minecraft:dirt", "count": 12}
    ]
  }
}
```

## Action

Sent by Python to update controlled input:

```json
{
  "type": "action",
  "movement": {
    "forward": true,
    "back": false,
    "left": false,
    "right": false,
    "jump": false,
    "sneak": false,
    "sprint": true
  },
  "look": {
    "yaw_delta": 5.0,
    "pitch_delta": 0.0
  },
  "attack": false,
  "use": false,
  "selected_slot": 0
}
```

## Release All

Sent by Python to immediately release all controlled input:

```json
{"type":"release_all"}
```
