#!/usr/bin/env python3
"""Open one immutable Office activation lock and exec the requested root script."""
import fcntl
import os
import stat
import sys

if len(sys.argv) < 5 or sys.argv[1] != "--lock" or sys.argv[3] != "--":
    raise SystemExit("usage: with-office-activation-lock.py --lock FILE -- COMMAND...")
lock = sys.argv[2]
flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(lock, flags, 0o600)
info = os.fstat(fd)
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit("unsafe activation lock")
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("another Office activation or rollback is running")
os.set_inheritable(fd, True)
if fd != 9:
    os.dup2(fd, 9, inheritable=True)
    os.close(fd)
env = os.environ.copy()
env["JAX_OFFICE_LOCK_HELD"] = f"{info.st_dev}:{info.st_ino}"
os.execvpe(sys.argv[4], sys.argv[4:], env)
