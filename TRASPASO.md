# Traspaso continuo — SR2 STEP_STATUS exact pair

Fecha: 2026-10-05

- Rama Platform: `k3/sr2-step-status-exact-pair` (PR #189).
- Base Platform: `64b6d1ff706d4d200e757460531c102676b8105a`, ya ancestro de
  esta rama; no se hizo un merge adicional.
- JAX exacto requerido: `200f03c7a17cbe1a488d7dab6921d841a8cd3a46` (JAX #341
  reconciliado después de la integración de #344).
- Los cuatro pins de `.github/workflows/policy.yml` deben conservar ese mismo
  SHA. Los pisos publicados se conservan: con DB `3676`, sin DB `2224`.
- Pendiente: esperar la CI del nuevo SHA Platform y pedir auditoría Tier 3 del
  par JAX/Platform exacto antes de integrar. No mezclar este par con F2-E
  tramo 2.
