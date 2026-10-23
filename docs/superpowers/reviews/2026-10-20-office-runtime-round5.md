# Task 1 fixround5 report · effective command flags

## RED

The round4 verifier inspected the legacy `ExecStart` D-Bus property.  Its
`a(sasbttttuii)` records do not preserve systemd command-prefix flags, so a
drop-in could keep the reviewed path, argv and `User=jaxweb` while executing
with an effective privilege or failure-handling override.

## GREEN

```text
python3 /tmp/cv-office-run-tests.py jxp --unit-no-db -q \
  tests/test_restore_artifact_readiness.py tests/test_office_db_provision.py
6 passed in 4.72s

python3 /tmp/cv-office-run-tests.py jxp -q --disable-warnings \
  tests/test_office_db_provision.py tests/test_office_runtime_contract.py \
  tests/test_office_authority.py tests/test_office_migrations.py \
  tests/test_migrate_readiness.py tests/test_restore_artifact_readiness.py \
  tests/test_auth.py tests/test_proyectos_api.py \
  tests/test_adjuntos_pdf_pool.py::test_lifespan_crea_y_cierra_el_pool
77 passed, 1093 warnings in 7.17s
```

`bash -n ops/migration/artifact-readiness.sh ops/migration/restore-hall9000.sh`,
`python3 -m py_compile ops/migration/artifact_readiness.py`, and
`git diff --check` also passed.

## Change

The verifier now queries only `ExecStartEx`, `ExecStartPreEx`,
`ExecStartPostEx`, and `ExecConditionEx`, all with the structured D-Bus
signature `a(sasasttttuii)`.  It accepts exactly one start record matching the
source-derived executable and argv and requires its flags array to be empty.
Every hook Ex property must be an empty array.  The verifier has no legacy
property fallback, so an unsupported or incomplete Ex reply denies readiness.

The faithful JSON D-Bus fixture covers a valid Ex contract, changed argv,
additional commands and hooks, legacy-only start/pre properties, and every
non-empty start flag category represented by the systemd Ex transport
(full-privileges, no-setuid, ignore-failure, argv0).  It retains the prior
identity, environment, dependency, type, artifact and malformed-response
denials.

## Scope

Only `ops/migration/artifact_readiness.py` and its focused test changed.  No
real systemd unit was loaded, reloaded, started, enabled or modified; no
production credential, account, backend authority path, commit or push was
changed by this task.

Root independently confirmed actual host Ex signature and privileged flag in existing legacy prehook via readonly busctl; no unit changes. Root focused6tests passed (see root command receipt); actual OS isolation/activation remains Task6.

## Independent verdict
Sol office_authority_review **APROBADO** exact094b23cb52c1d114251572bc21bb7a9163c9330c; priorBLOCK ADDRESSED, no openBLOCK/newregression in5e308fc→094b23c. Extendedsignaturematcheshost; exactsingleargv/emptyflags/emptyhooks/nolegacyfallback. This gate approves Task1 source/runtime contract, not liveOSisolation or productionactivation.
