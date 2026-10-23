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
# `hashlib.scrypt` libera el GIL. Cuatro KDF simultáneos caben en la memoria
# del runner estándar (~64 MiB para los estados de scrypt) y evitan que una
# lista corta de entradas limite el escaneo a dos núcleos.
MAX_WORKERS = 4
TOKENS_POR_TAREA = 512
MAX_DIFF_LINE_BYTES = 1024 * 1024
MAX_DIFF_BYTES = 128 * 1024 * 1024
MAX_GIT_BYTES = 32 * 1024 * 1024
MAX_LIST_BYTES = 1024 * 1024
MAX_ENTRIES = 100
SEPARADORES = re.compile(r"""[\s"'`,;()\[\]{}<>=:]+""")
RUNS = re.compile(rb"[\x20-\x7e]{%d,}" % MIN_TOKEN)


def fallar(motivo, detalle=""):
    print(f"::error::scan-pr-secrets: {motivo}")
    if detalle:
        print(detalle)
    sys.exit(1)


def git(args, binario=False):
    with subprocess.Popen(["git"] + args, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as proceso:
        salida = proceso.stdout.read(MAX_GIT_BYTES + 1)
        if len(salida) > MAX_GIT_BYTES:
            proceso.kill()
            proceso.wait()
            fallar("salida git demasiado grande (FAIL-CLOSED).")
        codigo = proceso.wait()
    if codigo != 0:
        fallar("git fallo durante el escaneo (FAIL-CLOSED).")
    return salida if binario else salida.decode("utf-8", "replace")


def lineas_diff(args):
    """Consume el diff sin almacenar su salida completa ni líneas sin límite."""
    with subprocess.Popen(["git"] + args, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as proceso:
        acumulado = 0
        while True:
            linea = proceso.stdout.readline(MAX_DIFF_LINE_BYTES + 1)
            if not linea:
                break
            acumulado += len(linea)
            if acumulado > MAX_DIFF_BYTES:
                proceso.kill()
                proceso.wait()
                fallar("diff demasiado grande (FAIL-CLOSED).")
            if len(linea) > MAX_DIFF_LINE_BYTES and not linea.endswith(b"\n"):
                proceso.kill()
                proceso.communicate()
                fallar("linea del diff demasiado grande (FAIL-CLOSED).")
            yield linea.rstrip(b"\n")
        if proceso.wait() != 0:
            fallar("git fallo durante el diff (FAIL-CLOSED).")


def cargar():
    try:
        if os.path.getsize(LISTA) > MAX_LIST_BYTES:
            fallar("la lista supera su límite de tamaño (FAIL-CLOSED).")
        crudo = open(LISTA, encoding="utf-8").read()
    except OSError as exc:
        fallar(f"no se pudo leer la lista (FAIL-CLOSED): {exc.strerror}", f"  {LISTA}")
    return parsear_lista(crudo)


def parsear_lista(crudo):
    if len(crudo.encode("utf-8")) > MAX_LIST_BYTES:
        fallar("la lista supera su límite de tamaño (FAIL-CLOSED).")
    entradas = []
    vistas = set()
    for num, linea in enumerate(crudo.splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        p = linea.split(None, 2)
        if (len(p) != 3 or not re.fullmatch(r"[0-9a-f]{32}", p[0])
                or not re.fullmatch(r"[0-9a-f]{64}", p[1])
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", p[2])):
            fallar("la lista no parsea (FAIL-CLOSED).",
                   f"  {LISTA}:{num} no tiene la forma '<salt-hex> <scrypt-hex> <etiqueta>'")
        entrada = (bytes.fromhex(p[0]), p[1], p[2])
        if entrada in vistas:
            fallar("la lista contiene entradas duplicadas (FAIL-CLOSED).")
        vistas.add(entrada)
        entradas.append(entrada)
        if len(entradas) > MAX_ENTRIES:
            fallar("la lista supera su límite de entradas (FAIL-CLOSED).")
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


def atributo_diff(ruta):
    salida = git(["check-attr", "-z", "diff", "--", ruta], binario=True)
    partes = salida.split(b"\x00")
    if (len(partes) != 4 or partes[0] != ruta.encode("utf-8", "surrogateescape")
            or partes[1] != b"diff" or partes[3] != b""):
        fallar("git check-attr no devolvió una respuesta exacta (FAIL-CLOSED).")
    return partes[2]


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


def tareas_de_comparacion(entradas, tokens):
    """Parte cada entrada en lotes, sin omitir ni repetir token/entrada.

    Antes había una tarea por entrada: la lista actual tiene dos, así que el
    runner nunca podía usar más de dos núcleos aunque el diff tuviera decenas
    de miles de tokens. El lote limita los Futures sin imponer un límite nuevo
    al contenido que ya pasó MAX_WORK_UNITS.
    """
    for entrada in entradas:
        for inicio in range(0, len(tokens), TOKENS_POR_TAREA):
            yield entrada, tokens[inicio:inicio + TOKENS_POR_TAREA]


def main():
    if len(sys.argv) != 3:
        fallar("uso: scan-pr-secrets.py <base-sha> <head-sha>")
    base, head = sys.argv[1], sys.argv[2]
    entradas = cargar()

    # Diff COMPLETO del PR (base...head), no el ultimo commit: asi cubre lo que
    # entro por merge, rebase o cherry-pick dentro de la rama.
    nombres = git(["diff", "--name-only", "-z", "--no-ext-diff",
                   "--diff-filter=ACMRT", f"{base}...{head}"], binario=True)
    rutas = [(x.decode("utf-8", "surrogateescape"), x)
             for x in nombres.split(b"\x00") if x]
    archivos = [ruta for ruta, _ in rutas if ruta not in EXCLUIDOS]

    # La lista del HEAD nunca decide contra qué se compara. Tampoco puede
    # quitar entradas para debilitar el escaneo de los PR siguientes.
    try:
        nueva = git(["show", f"{head}:ops/ci/known-secrets-ci.txt"], binario=True)
        actuales = parsear_lista(nueva.decode("utf-8"))
    except UnicodeError as exc:
        fallar(f"no se pudo verificar continuidad de la lista (FAIL-CLOSED): {exc}")
    if not set(entradas).issubset(set(actuales)):
        fallar("el PR elimina o altera entradas conocidas de la lista (FAIL-CLOSED).")

    # token -> todos los archivos que lo introducen. El KDF se hace una vez
    # por token/entrada y el informe conserva cada ruta afectada.
    cands, con_texto, archivo, en_hunk, ubicaciones = {}, set(), None, False, 0

    def agregar_candidato(token, ruta):
        nonlocal ubicaciones
        if len(token) < MIN_TOKEN:
            return
        rutas = cands.setdefault(token, set())
        if len(cands) > MAX_TOKENS:
            fallar(f"el diff supera el techo de {MAX_TOKENS} tokens unicos ({len(cands)}).")
        if len(cands) * len(entradas) > MAX_WORK_UNITS:
            fallar(f"el diff supera el techo de {MAX_WORK_UNITS} comparaciones scrypt ({len(cands) * len(entradas)}).")
        if ruta in rutas:
            return
        rutas.add(ruta)
        ubicaciones += 1
        if ubicaciones > MAX_UBICACIONES:
            fallar(f"el diff supera el techo de {MAX_UBICACIONES} ubicaciones archivo-token ({ubicaciones}).",
                   "  Se falla en vez de truncar: el mapa que conserva todas las rutas tambien debe ser acotado.")

    for a, ruta_cruda in rutas:
        if a in EXCLUIDOS:
            continue
        for token in tokens_de(a):
            agregar_candidato(token, a)
        for componente in re.split(r"[./\\]", a):
            for token in tokens_de(componente):
                agregar_candidato(token, a)
        for run in RUNS.findall(ruta_cruda):
            for token in tokens_de(run.decode("ascii")):
                agregar_candidato(token, a)

    for cruda in lineas_diff(["diff", "--unified=0", "--no-color",
                             "--no-ext-diff", "--no-textconv", "--diff-filter=ACMRT",
                             f"{base}...{head}"]):
        linea = cruda.decode("utf-8", "replace")
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
            for run in RUNS.findall(cruda[1:]):
                for t in tokens_de(run.decode("ascii")):
                    agregar_candidato(t, archivo)

    # Un blob con NUL o bytes no UTF-8 puede recibir un hunk textual aunque
    # el parser de texto no vea todos sus bytes. Se escanea entero en ese caso;
    # el texto UTF-8 normal usa hunks para no reprobar secretos ya existentes.
    # También se leen enteros los archivos sin hunk (binarios o diff apagado).
    for a in archivos:
        crudo = git(["show", f"{head}:{a}"], binario=True)
        if a in con_texto and b"\x00" not in crudo and atributo_diff(a) == b"unspecified":
            try:
                crudo.decode("utf-8")
                continue
            except UnicodeDecodeError:  # fail-soft: un blob no UTF-8 se escanea completo abajo
                pass
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

    # Los lotes dan paralelismo aunque haya pocas entradas conocidas. Cada
    # token/entrada aparece exactamente una vez y executor.map propaga una
    # excepción del KDF como fallo duro.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ejecutor:
        coincidencias = ejecutor.map(
            lambda tarea: comparar_entrada(*tarea),
            tareas_de_comparacion(entradas, unicos),
        )
        tokens_hallados = set()
        for hallados_entrada in coincidencias:
            tokens_hallados.update(hallados_entrada)
    hallados = [(archivo, etiqueta)
                for token, etiqueta in tokens_hallados
                for archivo in cands[token]]
    if hallados:
        for a, e in sorted(set(hallados)):
            ruta_digest = hashlib.sha256(a.encode("utf-8", "surrogateescape")).hexdigest()
            print(f"::error::secreto conocido '{e}' en ruta_sha256={ruta_digest}")
        fallar(f"{len(set(hallados))} ocurrencia(s) de secreto conocido en el diff del PR.",
               "  Esta barrera corre server-side: no la saltea --no-verify ni un\n"
               "  .gitattributes local. El valor debe salir del diff.")
    print("scan-pr-secrets: sin secretos conocidos en el diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
