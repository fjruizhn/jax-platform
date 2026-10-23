#!/usr/bin/env bash
# This historical tar/pip entrypoint must never publish a release. It cannot
# produce the build attestation and sealed-link inventory required by the
# builder/adopter handoff, so accepting its inputs would make an invalid tree
# visible under releases/ before readiness rejects it.
set -euo pipefail

echo 'install-platform-pair-release.sh is disabled: use the sealed jaxbuild builder and root adopter handoff' >&2
exit 64
