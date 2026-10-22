#!/usr/bin/env python3
"""Fail closed unless systemd's effective platform units match their source."""
from __future__ import annotations

import json
import ipaddress
import shlex
import subprocess
import sys
import os
import stat
from pathlib import Path


SERVICE_PROPERTIES = {
    "WorkingDirectory": "s", "User": "s", "Group": "s", "SupplementaryGroups": "as",
    "Environment": "as", "EnvironmentFiles": "a(sb)", "LoadCredential": "a(ss)", "UnsetEnvironment": "as", "ExecStartEx": "a(sasasttttuii)",
    "ExecStartPreEx": "a(sasasttttuii)", "ExecStartPostEx": "a(sasasttttuii)",
    "ExecConditionEx": "a(sasasttttuii)", "Type": "s", "RemainAfterExit": "b",
}
UNIT_PROPERTIES = {"Requires": "as", "After": "as"}
UNITS = ("jax-platform.service", "jax-platform-migrate.service")


class ReadinessError(Exception):
    pass


def fail(unit: str, field: str) -> ReadinessError:
    return ReadinessError(f"artifact readiness: {unit}: invalid effective {field}")


def source_contract(units_dir: Path, artifact: Path) -> dict[str, dict[str, object]]:
    contract: dict[str, dict[str, object]] = {}
    for unit in UNITS:
        sections: dict[str, list[tuple[str, str]]] = {"Unit": [], "Service": []}
        section = ""
        try:
            descriptor = os.open(units_dir / unit, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                value = os.fstat(descriptor)
                if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
                    raise OSError("unsafe source unit")
                chunks: list[bytes] = []
                while chunk := os.read(descriptor, 1024 * 1024):
                    chunks.append(chunk)
                lines = b"".join(chunks).decode("utf-8").splitlines()
            finally:
                os.close(descriptor)
        except (OSError, UnicodeDecodeError) as exc:
            raise ReadinessError(f"artifact readiness: {unit}: source unit unavailable") from exc
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line in ("[Unit]", "[Service]"):
                section = line[1:-1]
                continue
            if section in sections and "=" in line:
                key, value = line.split("=", 1)
                sections[section].append((key, value))

        def values(section_name: str, key: str) -> list[str]:
            return [value for candidate, value in sections[section_name] if candidate == key]

        cwd, users, groups, types, remain = (values("Service", key) for key in ("WorkingDirectory", "User", "Group", "Type", "RemainAfterExit"))
        environment, envs, creds, unset, starts = (values("Service", key) for key in ("Environment", "EnvironmentFile", "LoadCredential", "UnsetEnvironment", "ExecStart"))
        if len(cwd) != 1 or len(users) != 1 or len(groups) > 1 or len(types) != 1 or len(remain) > 1:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        if envs or len(creds) != 1 or len(starts) != 1:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        pre, post, condition = (values("Service", key) for key in ("ExecStartPre", "ExecStartPost", "ExecCondition"))
        if pre or post or condition:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        try:
            argv = shlex.split(starts[0])
            expected_environment = [item for value in environment for item in shlex.split(value)]
        except ValueError as exc:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed") from exc
        if not argv or starts[0].startswith("-") or creds[0].startswith("-"):
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        mode = "web" if unit == "jax-platform.service" else "migrate"
        credential = "runtime.env" if mode == "web" else "migrate.env"
        if cwd[0] != "/" or argv != ["/usr/bin/python3", "-I", "-S", "/usr/local/libexec/jax-platform-pair-exec.py", mode, f"${{CREDENTIALS_DIRECTORY}}/{credential}"]:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        effective_argv = [argument.replace("${CREDENTIALS_DIRECTORY}", f"/run/credentials/{unit}") for argument in argv]
        contract[unit] = {
            "WorkingDirectory": "/", "User": users[0], "Group": groups[0] if groups else "",
            "SupplementaryGroups": values("Service", "SupplementaryGroups"), "Environment": expected_environment,
            "EnvironmentFiles": [], "LoadCredential": [credential.split(":", 1) for credential in creds], "UnsetEnvironment": [item for value in unset for item in shlex.split(value)],
            "ExecStart": effective_argv, "ExecStartPre": [], "Type": types[0], "RemainAfterExit": remain == ["yes"],
            "Requires": [item for value in values("Unit", "Requires") for item in value.split()],
            "After": [item for value in values("Unit", "After") for item in value.split()],
        }
        if not contract[unit]["Requires"] or not contract[unit]["After"]:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
    return contract


def dbus_json(command: list[str], args: list[str], unit: str, field: str, expected_type: str) -> object:
    try:
        result = subprocess.run(command + ["--json=short"] + args, text=True, capture_output=True, check=True)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise fail(unit, field) from exc
    if not isinstance(payload, dict) or payload.get("type") != expected_type or "data" not in payload:
        raise fail(unit, field)
    return payload["data"]


def effective_contract(command: list[str], unit: str) -> dict[str, object]:
    object_path = dbus_json(command, ["call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "LoadUnit", "s", unit], unit, "LoadUnit", "o")
    if not isinstance(object_path, list) or len(object_path) != 1 or not isinstance(object_path[0], str) or not object_path[0].startswith("/org/freedesktop/systemd1/unit/"):
        raise fail(unit, "LoadUnit")
    path = object_path[0]
    values: dict[str, object] = {}
    for field, type_name in SERVICE_PROPERTIES.items():
        values[field] = dbus_json(command, ["get-property", "org.freedesktop.systemd1", path, "org.freedesktop.systemd1.Service", field], unit, field, type_name)
    for field, type_name in UNIT_PROPERTIES.items():
        values[field] = dbus_json(command, ["get-property", "org.freedesktop.systemd1", path, "org.freedesktop.systemd1.Unit", field], unit, field, type_name)
    return values


def verify(artifact: Path, command: list[str], units_dir: Path) -> None:
    expected = source_contract(units_dir, artifact)
    for unit in UNITS:
        actual = effective_contract(command, unit)
        wanted = expected[unit]
        for field in ("WorkingDirectory", "User", "Group", "SupplementaryGroups", "Environment", "EnvironmentFiles", "LoadCredential", "UnsetEnvironment", "Type", "RemainAfterExit"):
            if actual[field] != wanted[field]:
                raise fail(unit, field)
        # ExecStart deliberately has no privilege-prefix information.  The
        # *Ex properties retain it as the third tuple member (an array of
        # flags), so accepting the legacy property could silently run a
        # different command as root or with no-setuid/ignore-failure flags.
        starts = actual["ExecStartEx"]
        if not isinstance(starts, list) or len(starts) != 1 or not isinstance(starts[0], list) or len(starts[0]) != 10:
            raise fail(unit, "ExecStartEx")
        if (not isinstance(starts[0][0], str)
                or not isinstance(starts[0][1], list)
                or not all(isinstance(argument, str) for argument in starts[0][1])
                or not isinstance(starts[0][2], list)
                or starts[0][2] != []
                or starts[0][0] != wanted["ExecStart"][0]):
            raise fail(unit, "ExecStartEx")
        actual_argv = starts[0][1]
        expected_argv = wanted["ExecStart"]
        if actual_argv != expected_argv:
            raise fail(unit, "ExecStartEx")
        pre = actual["ExecStartPreEx"]
        expected_pre = wanted["ExecStartPre"]
        if expected_pre == []:
            if pre != []:
                raise fail(unit, "ExecStartPreEx")
        elif (not isinstance(pre, list) or len(pre) != 1 or not isinstance(pre[0], list) or len(pre[0]) != 10
                or pre[0][0] != expected_pre[0] or pre[0][1] != expected_pre or pre[0][2] != []):
            raise fail(unit, "ExecStartPreEx")
        for field in ("ExecStartPostEx", "ExecConditionEx"):
            if actual[field] != []:
                raise fail(unit, field)
        for field in ("Requires", "After"):
            actual_dependencies = actual[field]
            if not isinstance(actual_dependencies, list) or not all(isinstance(value, str) for value in actual_dependencies) or not set(wanted[field]).issubset(actual_dependencies):
                raise fail(unit, field)


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[2] != "--units-dir":
        return 2
    try:
        verify(Path(argv[1]), argv[4:], Path(argv[3]))
    except ReadinessError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
