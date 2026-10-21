# Task 1 fixround4 report · effective unit contract

## RED

`CI=true backend/.venv/bin/pytest -q backend/tests/test_restore_artifact_readiness.py`

Result: the valid D-Bus JSON fixture failed with `artifact readiness: cannot
inspect jax-platform.service`.  The previous implementation called human
formatted `systemctl show`, so it could neither consume the required
`busctl --json=short` transport nor verify the effective fields.

## GREEN

`python3 /tmp/cv-office-run-tests.py jxp --unit-no-db -q tests/test_restore_artifact_readiness.py tests/test_office_db_provision.py`

Result: `6 passed in 4.35s`.

`bash -n ops/migration/artifact-readiness.sh ops/migration/restore-hall9000.sh`

`python3 -m py_compile ops/migration/artifact_readiness.py`

`git diff --check`

Result: all passed with no output.

## Change

`artifact-readiness.sh` delegates to a small Python verifier.  It derives the
expected values from the reviewed source unit files, adjusts only the release
artifact root, asks systemd to `LoadUnit`, and reads each effective property
through structured `busctl --json=short` replies.  It validates both service
users, group fields, environment and environment files, working directory,
full command vectors, command hooks, type, `RemainAfterExit`, and the required
dependency ordering.  Invalid, incomplete, or unsupported replies fail closed
without printing environment content or command metadata.

Restore now invokes that verifier through `sudo busctl` after its existing
`daemon-reload`, before any start or enable step.  It performs no reload,
start, enable, or other mutation itself.

The fixture is a temporary executable fake of the actual D-Bus JSON protocol.
It covers the valid contract and rejects wrong argv/user/group/supplementary
group, injected environment, common or ignored environment file, additional
start/pre/post/condition commands, missing migration/docker dependency, wrong
type, `RemainAfterExit`, missing artifact executable, and corrupt, unsupported,
or incomplete replies.

## Self-review

- The verifier reads effective properties and never parses `systemctl` display
  text.
- Source templates remain the expected-value authority; the valid test uses
  their actual current defaults, so source drift breaks the contract test.
- Unit observation uses `LoadUnit` only.  No real systemd unit or host secret
  was modified or read.
- No backend authority, migration, authentication, documentation, commits, or
  pushes were changed by this task.

## Independent root verification
2026-10-20 · Codex: 77 focused/runtime/authority/migration/auth/project tests passed in6.32s against disposable MariaDB33991; 1093 expected idempotent-DDL warnings hidden. Read-only live busctl scalar/array formats matched fixture JSON type/data transport; live service remains legacy and was not changed. No OS isolation/boot or production claim; Task6 remains.
