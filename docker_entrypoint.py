"""Entrypoint del contenedor: aplica migraciones y ejecuta el comando indicado.

Es un script Python (no shell) para no depender de ``/bin/sh`` en la imagen, dejando el
camino abierto a una base *distroless* sin shell. Ejecuta ``alembic upgrade head`` y luego
reemplaza el proceso por el comando recibido (CMD) con ``os.execvp``, de modo que uvicorn
herede las señales (SIGTERM) y el contenedor se detenga limpiamente.
"""

import os
import sys

from alembic import command
from alembic.config import Config


def main() -> None:
    print("Aplicando migraciones (alembic upgrade head)...", flush=True)
    command.upgrade(Config("alembic.ini"), "head")

    argv = sys.argv[1:]
    if not argv:
        raise SystemExit("entrypoint: falta el comando a ejecutar")
    print("Arrancando la aplicación...", flush=True)
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
