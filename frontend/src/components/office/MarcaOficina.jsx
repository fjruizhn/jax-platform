// OP 358 pieza 2 — marca de la Oficina Virtual: el brandmark de los studios
// (cuadrícula sobre acento, borde y sombra duros del color de tinta, giro
// -4°) en lugar del ojo de Hal. Estilos en index.css (.marca-oficina); la
// monta Login (y la barra de la oficina) sólo en la instalación de oficina.
export default function MarcaOficina({ etiqueta }) {
  return (
    <div className="marca-oficina" role="img" aria-label={etiqueta}>▦</div>
  )
}
