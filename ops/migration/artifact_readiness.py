#!/usr/bin/env python3
"""Fail closed unless systemd's effective platform units match their source."""
from __future__ import annotations

import json
import ipaddress
import shlex
import subprocess
import sys
from pathlib import Path


SERVICE_PROPERTIES = {
    "WorkingDirectory": "s", "User": "s", "Group": "s", "SupplementaryGroups": "as",
    "Environment": "as", "EnvironmentFiles": "a(sb)", "ExecStartEx": "a(sasasttttuii)",
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
            lines = (units_dir / unit).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
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
        environment, envs, starts = values("Service", "Environment"), values("Service", "EnvironmentFile"), values("Service", "ExecStart")
        if len(cwd) != 1 or len(users) != 1 or len(groups) > 1 or len(types) != 1 or len(remain) > 1:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        if len(envs) != 1 or len(starts) != 1:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        pre, post, condition = (values("Service", key) for key in ("ExecStartPre", "ExecStartPost", "ExecCondition"))
        if post or condition or len(pre) != 1 or pre[0] != "/usr/local/libexec/jax-platform-pair-artifact-readiness":
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        try:
            argv = shlex.split(starts[0])
            expected_environment = [item for value in environment for item in shlex.split(value)]
        except ValueError as exc:
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed") from exc
        if not argv or starts[0].startswith("-") or envs[0].startswith("-"):
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        source_backend = cwd[0]
        expected_backend = str(artifact / "backend")
        if not source_backend.startswith("/") or not argv[0].startswith(source_backend + "/"):
            raise ReadinessError(f"artifact readiness: {unit}: source unit malformed")
        expected_argv = [expected_backend + argument[len(source_backend):] if argument.startswith(source_backend + "/") else argument for argument in argv]
        contract[unit] = {
            "WorkingDirectory": expected_backend, "User": users[0], "Group": groups[0] if groups else "",
            "SupplementaryGroups": values("Service", "SupplementaryGroups"), "Environment": expected_environment,
            "EnvironmentFiles": [[envs[0], False]],
            "ExecStart": expected_argv, "ExecStartPre": pre, "Type": types[0], "RemainAfterExit": remain == ["yes"],
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


def verify(artifact: Path, command: list[str]) -> None:
    backend = artifact / "backend"
    if not backend.is_dir():
        raise ReadinessError("artifact readiness: artifact: missing backend")
    for unit, executable in ((UNITS[0], backend / ".venv/bin/uvicorn"), (UNITS[1], backend / ".venv/bin/python")):
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            raise ReadinessError(f"artifact readiness: {unit}: missing executable")
    expected = source_contract(Path(__file__).with_name("systemd-units"), artifact)
    for unit in UNITS:
        actual = effective_contract(command, unit)
        wanted = expected[unit]
        for field in ("WorkingDirectory", "User", "Group", "SupplementaryGroups", "Environment", "EnvironmentFiles", "Type", "RemainAfterExit"):
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
        if unit == "jax-platform.service":
            placeholder = "--forwarded-allow-ips=${JAX_TRUSTED_PROXIES}"
            if placeholder not in expected_argv:
                raise fail(unit, "ExecStartEx")
            position = expected_argv.index(placeholder)
            if (len(actual_argv) != len(expected_argv)
                    or actual_argv[:position] != expected_argv[:position]
                    or actual_argv[position + 1:] != expected_argv[position + 1:]):
                raise fail(unit, "ExecStartEx")
            proxy_arg = actual_argv[position]
            if not proxy_arg.startswith("--forwarded-allow-ips="):
                raise fail(unit, "ExecStartEx")
            try:
                proxies = tuple(value for value in proxy_arg.split("=", 1)[1].split(",") if value)
                if not proxies or len(proxies) != len(set(proxies)):
                    raise ValueError
                for proxy in proxies:
                    ipaddress.ip_address(proxy)
            except ValueError:
                raise fail(unit, "ExecStartEx") from None
        elif actual_argv != expected_argv:
            raise fail(unit, "ExecStartEx")
        pre = actual["ExecStartPreEx"]
        expected_pre = wanted["ExecStartPre"]
        if (not isinstance(pre, list) or len(pre) != 1 or not isinstance(pre[0], list) or len(pre[0]) != 10
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
    if len(argv) < 3:
        return 2
    try:
        verify(Path(argv[1]), argv[2:])
    except ReadinessError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
