#!/usr/bin/env python3
"""Manual debug client for the YRush bot Fabric bridge."""

from __future__ import annotations

import json
import socket
import sys
import threading
from dataclasses import dataclass, field
from typing import Any


HOST = "127.0.0.1"
PORT = 47321


@dataclass
class MovementState:
    forward: bool = False
    back: bool = False
    left: bool = False
    right: bool = False
    jump: bool = False
    sneak: bool = False
    sprint: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "forward": self.forward,
            "back": self.back,
            "left": self.left,
            "right": self.right,
            "jump": self.jump,
            "sneak": self.sneak,
            "sprint": self.sprint,
        }


@dataclass
class DebugClient:
    sock: socket.socket
    movement: MovementState = field(default_factory=MovementState)
    attack: bool = False
    use: bool = False
    latest_observation: dict[str, Any] | None = None

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")) + "\n"
        self.sock.sendall(payload.encode("utf-8"))

    def send_action(self, **extra: Any) -> None:
        message: dict[str, Any] = {
            "type": "action",
            "movement": self.movement.as_dict(),
            "attack": self.attack,
            "use": self.use,
        }
        message.update(extra)
        self.send(message)

    def release_all(self) -> None:
        self.movement = MovementState()
        self.attack = False
        self.use = False
        self.send({"type": "release_all"})


def read_loop(client: DebugClient) -> None:
    file = client.sock.makefile("r", encoding="utf-8")
    for line in file:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(f"invalid json from bridge: {line.rstrip()}")
            continue

        if message.get("type") == "observation":
            client.latest_observation = message
            player = message.get("player", {})
            yrush = message.get("yrush", {})
            inventory = message.get("inventory", {})
            summary = inventory.get("summary", {})
            objective = ""
            if yrush.get("source") == "packet":
                objective = (
                    f" yrush={yrush.get('phase')}"
                    f" pursue={yrush.get('should_pursue_objective')}"
                    f" target={yrush.get('target_y')}"
                    f" remaining={yrush.get('distance_remaining')}"
                    f" time={yrush.get('seconds_remaining')}"
                )
            elif yrush.get("objective_known"):
                objective = f" objective={yrush.get('direction')}:{yrush.get('distance_total')}"
            inventory_text = ""
            if summary:
                inventory_text = (
                    f" blocks={summary.get('placeable_blocks', 0)}"
                    f" logs={summary.get('logs', 0)}"
                    f" pickaxe={summary.get('best_pickaxe', 'none')}"
                )
            if player.get("present"):
                print(
                    "obs "
                    f"x={player.get('x'):.2f} "
                    f"y={player.get('y'):.2f} "
                    f"z={player.get('z'):.2f} "
                    f"yaw={player.get('yaw'):.1f} "
                    f"pitch={player.get('pitch'):.1f}"
                    f"{inventory_text}"
                    f"{objective}"
                )
        else:
            print(json.dumps(message, indent=2))


def set_bool(value: str) -> bool:
    if value in {"on", "true", "1"}:
        return True
    if value in {"off", "false", "0"}:
        return False
    raise ValueError("expected on/off")


def print_help() -> None:
    print(
        "commands: w/a/s/d on|off, jump on|off, sneak on|off, sprint on|off, "
        "look <yaw_delta> <pitch_delta>, attack on|off, use on|off, slot 0-8, "
        "release, obs, inventory, yrush, quit"
    )


def command_loop(client: DebugClient) -> None:
    print_help()
    while True:
        try:
            raw = input("> ").strip()
        except EOFError:
            break

        if not raw:
            continue

        parts = raw.split()
        command = parts[0].lower()

        try:
            if command in {"quit", "exit"}:
                client.release_all()
                return
            if command == "help":
                print_help()
            elif command == "release":
                client.release_all()
            elif command == "obs":
                print(json.dumps(client.latest_observation, indent=2))
            elif command == "yrush":
                observation = client.latest_observation or {}
                print(json.dumps(observation.get("yrush"), indent=2))
            elif command == "inventory":
                observation = client.latest_observation or {}
                print(json.dumps(observation.get("inventory"), indent=2))
            elif command in {"w", "a", "s", "d", "jump", "sneak", "sprint"}:
                if len(parts) != 2:
                    raise ValueError("expected: command on|off")
                enabled = set_bool(parts[1].lower())
                field_name = {
                    "w": "forward",
                    "a": "left",
                    "s": "back",
                    "d": "right",
                }.get(command, command)
                setattr(client.movement, field_name, enabled)
                client.send_action()
            elif command == "look":
                if len(parts) != 3:
                    raise ValueError("expected: look <yaw_delta> <pitch_delta>")
                client.send_action(
                    look={
                        "yaw_delta": float(parts[1]),
                        "pitch_delta": float(parts[2]),
                    }
                )
            elif command == "attack":
                if len(parts) != 2:
                    raise ValueError("expected: attack on|off")
                client.attack = set_bool(parts[1].lower())
                client.send_action()
            elif command == "use":
                if len(parts) != 2:
                    raise ValueError("expected: use on|off")
                client.use = set_bool(parts[1].lower())
                client.send_action()
            elif command == "slot":
                if len(parts) != 2:
                    raise ValueError("expected: slot 0-8")
                slot = int(parts[1])
                if not 0 <= slot <= 8:
                    raise ValueError("slot must be 0-8")
                client.send_action(selected_slot=slot)
            else:
                print(f"unknown command: {command}")
        except (ValueError, OSError) as ex:
            print(f"error: {ex}")


def main() -> int:
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        client = DebugClient(sock)
        reader = threading.Thread(target=read_loop, args=(client,), daemon=True)
        reader.start()
        command_loop(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
