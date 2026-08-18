# Producer operations

Hermes runs as a foreground, non-executing signal producer. Process supervision, restart policy, log collection, working directory, and host networking belong to the service/container environment rather than to Hermes.

## Canonical foreground commands

From a Hermes checkout with the locked runtime environment available:

```bash
uv run --no-dev python -m weatherbot.producer scan
uv run --no-dev python -m weatherbot.producer status
uv run --no-dev python -m weatherbot.producer run
```

`bot_v3.py` remains a compatibility entrypoint, but the package command is the operational authority for development, service managers, and containers.

The producer resolves repository-owned configuration and relative state paths from the installed/source package location. It does not contain a fixed clone path or virtual-environment path.

## Foreground process contract

`run` stays in the foreground. Hermes does not daemonize itself, write a daemon PID file, manage duplicate instances, or discover processes by command-line matching.

A service manager or container runtime should execute exactly one foreground producer process and own singleton/restart behavior. The same command is suitable for an interactive shell, systemd `ExecStart`, or a container command.

On `SIGINT` or `SIGTERM`, Hermes records the shutdown request, lets the current bounded scan finish, does not begin another scan, restores the prior signal handlers, and exits. The conventional exit statuses are `130` for `SIGINT` and `143` for `SIGTERM`.

One-shot commands retain their own statuses: `scan` returns `0` when clean and `1` when operational errors were collected; invalid startup/configuration returns `2`.

## Network and proxy policy

Hermes inherits networking from the process environment that starts it. It never clears or rewrites unrelated proxy variables.

If deployment requires an HTTP(S) proxy, configure the standard process environment for the Hermes service/container. If routing is supplied by the host network or a TUN/VPN interface, start Hermes with the desired service environment and no proxy override.

Hermes contains no workstation-specific knowledge of VPNs, TUN interfaces, SOCKS endpoints, or local proxy topology.

## Credentials and execution boundary

Normal public producer operation requires no wallet, exchange-write credential, bankroll, position, or trading ledger. Public market access is read-only.

PIP (Prediction Intelligence Platform) publication uses a separate application-identity signing key only when explicitly enabled. It is not a wallet key and has no financial authority.

## Optional PIP delivery worker

PIP delivery is an independent foreground process:

```bash
uv run --no-dev python -m weatherbot.pip run
```

The producer may stage/reconcile durable local PIP outbox state, but it never performs the remote PIP POST itself. The delivery worker owns network delivery and retry state.

A PIP outage therefore cannot change whether Hermes generated a signal, its identity, or its durable local signal record. Delivery state may remain pending/retrying until PIP is available again.

On `SIGINT` or `SIGTERM`, the worker lets the current bounded delivery attempt finish, persists the resulting outbox transition, does not claim another item, and exits with the corresponding conventional signal exit status.

## Service/container responsibility split

```text
Hermes producer     -> signal generation + local durability
PIP worker          -> outbox delivery
service supervisor  -> process identity + restart/singleton policy
host/container      -> networking + proxy policy
```

The removed background shell helpers are intentionally not replaced. This avoids stale PID state, broad process matching, fixed clone paths, fixed virtual environments, and hidden mutation of host networking configuration.
