# 06 — Provision PostgreSQL for the render engine

Status: ready-for-human
Type: task

## Question

CS:DM 3.x refuses to run without a PostgreSQL 17+ server: the CLI's `analyze` command opens a Kysely
connection and dies with `ECONNREFUSED 127.0.0.1:5432`. Nothing in the pipeline works until this
exists.

## What CS:DM expects

Its settings file (generated at `<appFolder>/settings.json`, which we now redirect into the repo's
`home/.csdm/` via a `USERPROFILE` override) ships these defaults:

```json
"database": { "hostname": "127.0.0.1", "port": 5432, "username": "postgres",
              "password": "password", "database": "csdm" }
```

Its docs require **PostgreSQL 17 or later**, and **`psql` must be on `PATH`** because CS:DM shells out
to it to create the `csdm` database. A remote database is supported; `PGSSLMODE=require` is the
documented workaround for insecure-connection errors.

## Steps

1. Install PostgreSQL 17+ and start the service.
2. Put `psql` on `PATH` (`C:\Program Files\PostgreSQL\17\bin`).
3. Write the chosen password into `home/.csdm/settings.json` so the CLI can connect unattended.
4. Re-run: `USERPROFILE=<repo>/home csdm analyze <demo> --source faceit`.

## Note

This is a heavier dependency than the ADR anticipated — CS:DM is not self-contained. It does not
change the "use CS:DM as the engine" decision (the alternative is reimplementing tick-accurate demo
playback plus a CS2 server plugin), but it does add a service to the machine and to the setup story.

## Answer

_(unresolved)_
