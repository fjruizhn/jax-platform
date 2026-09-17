// Nombre visible de una faceta (revisión final, menor 6c): display_name de
// /api/state (store `facets`), luego name, y el id sólo si no hay otro. Lo
// comparten PipelineModal, BottomBar y ContinuarPipelineModal: una sola regla.
export function nombreDeFaceta(facetsState, id) {
  const faceta = facetsState?.[id]
  return faceta?.display_name || faceta?.name || id
}
