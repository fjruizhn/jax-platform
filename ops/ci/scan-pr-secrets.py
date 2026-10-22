#!/usr/bin/env python3
"""Barrera de contenido SERVER-SIDE: rechaza un PR que introduzca un secreto
ya conocido, en cualquier parte de su diff completo.

POR QUE EXISTE, con lo que la motivo (medido 2026-09-01): los hooks de
pre-commit NO corren en `merge`, `rebase`, `cherry-pick`, `revert` ni `stash`
-- solo en `commit` y `commit --amend`. Y el `pre-push` mira el ref DESTINO,
no el contenido. Consecuencia: un commit hecho con `--no-verify`, o anterior a
la activacion del hook, entraba a `master` por merge SIN pasar por ninguna
revision de contenido. Este check cierra esa clase, porque corre del lado del
servidor sobre el diff `base...head` completo del PR y no lo saltea ningun
flag local.

POR QUE NO USA EL PEPPER: ver la cabecera de ops/ci/known-secrets-ci.txt. Salt
publico por entrada + scrypt de costo alto, en vez de meter el pepper como
secret de Actions y crear superficie nueva.

FAIL-CLOSED (P10): lista ilegible, no parseable o vacia => el check FALLA. Un
comparador sin con que comparar no encontro cero: no comparo.

QUE CUBRE que el hook local no cubre: merge, rebase, cherry-pick, revert, y
cualquier commit que haya entrado con --no-verify.
QUE NO CUBRE, declarado: un secreto NUEVO (no esta en la lista); un secreto
embebido sin separadores alrededor; y codificaciones alternativas (base64,
URL-encode) del valor.
"""
import hashlib
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

AQUI = os.path.dirname(os.path.abspath(__file__))
LISTA = os.environ.get("JAX_CI_SECRETS", os.path.join(AQUI, "known-secrets-ci.txt"))
MIN_TOKEN = 6
# La propia lista se excluye del escaneo: es la REFERENCIA contra la que se
# compara, no contenido a revisar. Solo lleva hashes y salts.
EXCLUIDOS = {"ops/ci/known-secrets-ci.txt"}
# Ambos limites son una barrera de disponibilidad, no una optimizacion que
# pueda truncar el escaneo. MAX_WORK_UNITS tambien hace que una futura lista
# grande no multiplique en silencio el costo de scrypt.
MAX_TOKENS = 50000
MAX_WORK_UNITS = 100000
MAX_UBICACIONES = 100000
MAX_WORKERS = 2
SEPARADORES = re.compile(r"""[\s"'`,;()\[\]{}<>=:]+""")
RUNS = re.compile(rb"[\x20-\x7e]{%d,}" % MIN_TOKEN)


def fallar(motivo, detalle=""):
    print(f"::error::scan-pr-secrets: {motivo}")
    if detalle:
        print(detalle)
    sys.exit(1)


def git(args, binario=False):
    out = subprocess.run(["git"] + args, capture_output=True, check=False,
                         **({} if binario else {"text": True, "errors": "replace"}))
    if out.returncode != 0:
        err = out.stderr if not binario else out.stderr.decode("utf-8", "replace")
        fallar("git fallo durante el escaneo (FAIL-CLOSED).",
               f"  git {' '.join(args)}\n  {str(err).strip()[:300]}")
    return out.stdout


def cargar():
    try:
        crudo = open(LISTA, encoding="utf-8").read()
    except OSError as exc:
        fallar(f"no se pudo leer la lista (FAIL-CLOSED): {exc.strerror}", f"  {LISTA}")
    entradas = []
    for num, linea in enumerate(crudo.splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        p = linea.split(None, 2)
        if len(p) != 3 or not re.fullmatch(r"[0-9a-f]{32}", p[0]) or not re.fullmatch(r"[0-9a-f]{64}", p[1]):
            fallar("la lista no parsea (FAIL-CLOSED).",
                   f"  {LISTA}:{num} no tiene la forma '<salt-hex> <scrypt-hex> <etiqueta>'")
        entradas.append((bytes.fromhex(p[0]), p[1], p[2]))
    if not entradas:
        fallar("la lista esta vacia (FAIL-CLOSED).",
               "  Un comparador sin nada con que comparar no encontro cero: no comparo.")
    return entradas


def tokens_de(texto):
    vistos = {texto.strip()}
    for t in SEPARADORES.split(texto):
        if t:
            vistos.add(t)
            vistos.add(t.strip("-_./\\"))
    return {t for t in vistos if len(t) >= MIN_TOKEN}


def comparar_entrada(entrada, tokens):
    """Devuelve los tokens que coinciden con una sola entrada conocida.

    Cada token llega aqui una sola vez por entrada. La concurrencia se limita
    en main() a dos entradas a la vez para no convertir el escaneo en una
    tormenta de memoria/CPU cuando crezca la lista.
    """
    salt, esperado, etiqueta = entrada
    hallados = set()
    for token in tokens:
        digest = hashlib.scrypt(token.encode("utf-8", "replace"), salt=salt,
                                n=2**14, r=8, p=1, dklen=32).hex()
        if digest == esperado:
            hallados.add((token, etiqueta))
    return hallados


def main():
    if len(sys.argv) != 3:
        fallar("uso: scan-pr-secrets.py <base-sha> <head-sha>")
    base, head = sys.argv[1], sys.argv[2]
    entradas = cargar()

    # Diff COMPLETO del PR (base...head), no el ultimo commit: asi cubre lo que
    # entro por merge, rebase o cherry-pick dentro de la rama.
    archivos = [x for x in git(["diff", "--name-only", "--diff-filter=ACMRT",
                                f"{base}...{head}"]).splitlines()
                if x and x not in EXCLUIDOS]
    diff = git(["diff", "--unified=0", "--no-color", "--diff-filter=ACMRT",
                f"{base}...{head}"])

    # token -> todos los archivos que lo introducen. El KDF se hace una vez
    # por token/entrada y el informe conserva cada ruta afectada.
    cands, con_texto, archivo, en_hunk, ubicaciones = {}, set(), None, False, 0

    def agregar_candidato(token, ruta):
        nonlocal ubicaciones
        rutas = cands.setdefault(token, set())
        if ruta in rutas:
            return
        rutas.add(ruta)
        ubicaciones += 1
        if ubicaciones > MAX_UBICACIONES:
            fallar(f"el diff supera el techo de {MAX_UBICACIONES} ubicaciones archivo-token ({ubicaciones}).",
                   "  Se falla en vez de truncar: el mapa que conserva todas las rutas tambien debe ser acotado.")

    for linea in diff.splitlines():
        if linea.startswith("diff --git "):
            archivo, en_hunk = None, False
        elif linea.startswith("+++ ") and not en_hunk:
            archivo = linea[6:] if linea.startswith("+++ b/") else linea[4:]
        elif linea.startswith("@@"):
            en_hunk = True
        elif en_hunk and linea.startswith("+"):
            if archivo in EXCLUIDOS:
                continue
            if archivo:
                con_texto.add(archivo)
            for t in tokens_de(linea[1:]):
                agregar_candidato(t, archivo)

    # Binarios y rutas con el diff apagado por .gitattributes: sin lineas '+'.
    # Se leen enteros desde head. Es la clase del .pyc que origino todo esto.
    for a in archivos:
        if a in con_texto:
            continue
        crudo = git(["show", f"{head}:{a}"], binario=True)
        for run in RUNS.findall(crudo):
            for t in tokens_de(run.decode("ascii", "replace")):
                agregar_candidato(t, a)

    unicos = tuple(sorted(cands))
    if len(unicos) > MAX_TOKENS:
        fallar(f"el diff supera el techo de {MAX_TOKENS} tokens unicos ({len(unicos)}).",
               "  Se falla en vez de truncar: un escaneo parcial que dice 'limpio' es fail-open.")
    trabajo = len(unicos) * len(entradas)
    if trabajo > MAX_WORK_UNITS:
        fallar(f"el diff supera el techo de {MAX_WORK_UNITS} comparaciones scrypt ({trabajo}).",
               "  Se falla en vez de truncar: una lista futura no puede degradar el escaneo hasta hacerlo inusable.")

    print(f"scan-pr-secrets: {len(archivos)} archivos, {len(unicos)} tokens unicos, "
          f"{ubicaciones} ubicaciones, {len(entradas)} entradas conocidas, {trabajo} comparaciones")

    # Una tarea por entrada, nunca por token: como maximo dos workers y no
    # miles de Futures. executor.map conserva las excepciones como fallo duro.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ejecutor:
        coincidencias = ejecutor.map(lambda entrada: comparar_entrada(entrada, unicos), entradas)
        tokens_hallados = set().union(*coincidencias)
    hallados = [(archivo, etiqueta)
                for token, etiqueta in tokens_hallados
                for archivo in cands[token]]
    if hallados:
        for a, e in sorted(set(hallados)):
            print(f"::error file={a}::secreto conocido '{e}' introducido por este PR")
        fallar(f"{len(set(hallados))} ocurrencia(s) de secreto conocido en el diff del PR.",
               "  Esta barrera corre server-side: no la saltea --no-verify ni un\n"
               "  .gitattributes local. El valor debe salir del diff.")
    print("scan-pr-secrets: sin secretos conocidos en el diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
