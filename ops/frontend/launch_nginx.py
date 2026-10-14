#!/usr/bin/env python3
"""Exec nginx with reopenable stdout/stderr pipes forwarding to manager logs.

journald supplies AF_UNIX sockets, which cannot be reopened through /dev/stdout.
The renderer intentionally logs to stdout/stderr. Two short-lived children copy
pipes to the inherited descriptors; the original PID execs nginx unchanged.
No background service or checkout dependency is introduced.
"""
import argparse
import os
import sys


def pipe_to(destination,close_fds=()):
    reader,writer=os.pipe()
    pid=os.fork()
    if pid==0:
        os.close(writer)
        for descriptor in close_fds: os.close(descriptor)
        try:
            while True:
                block=os.read(reader,65536)
                if not block: break
                while block:
                    count=os.write(destination,block); block=block[count:]
        except OSError: pass
        finally: os._exit(0)
    os.close(reader)
    return writer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',required=True)
    parser.add_argument('--config',required=True)
    parser.add_argument('--test',action='store_true')
    args=parser.parse_args()
    for value in (args.binary,args.config):
        if not value.startswith('/') or any(char.isspace() for char in value): raise ValueError('absolute unambiguous paths required')
    stdout=pipe_to(1); stderr=pipe_to(2,(stdout,))
    os.dup2(stdout,1); os.dup2(stderr,2)
    os.close(stdout); os.close(stderr)
    command=[args.binary,*(['-t'] if args.test else ['-g','daemon off;']),'-c',args.config]
    try: os.execv(args.binary,command)
    except OSError as error:
        os.write(2,('nginx exec failed: '+str(error)+'\n').encode())
        os._exit(127)

if __name__=='__main__': main()
