# Office critic receipt v1

This slice verifies a canonical Ed25519 `rights.review` envelope in memory.
The signed bytes bind exactly one candidate revision, manifest digest and
artifact-set digest.  The caller injects the public-key resolver; without a
separately governed trust-key registry there is no production key fallback.

It stores nothing and makes no release, creative approval, rights-verdict
ledger, or publication decision.  Those authorities remain pending contract.
