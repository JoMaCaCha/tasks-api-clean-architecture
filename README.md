# Crehana Tasks API — Desafío Técnico Backend

API REST de gestión de **listas de tareas** construida con **FastAPI**, **SQLAlchemy 2.0
async** (PostgreSQL + `asyncpg`), **Pydantic V2** y **arquitectura limpia** (cuatro capas).

> **¿Tienes prisa?** Si solo quieres verla funcionando, salta a
> [Inicio rápido con Docker](#inicio-rápido-con-docker-recomendado): son dos comandos.

## Tabla de contenidos

- [¿Qué es y qué resuelve?](#qué-es-y-qué-resuelve)
- [Arquitectura por capas](#arquitectura-por-capas)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Endpoints](#endpoints-principales-prefijo-apiv1)
- [Prerrequisitos](#prerrequisitos)
- [Inicio rápido con Docker](#inicio-rápido-con-docker-recomendado)
- [Configuración del entorno local (sin Docker)](#configuración-del-entorno-local-sin-docker)
- [Variables de entorno](#variables-de-entorno)
- [Ejemplo de uso (flujo completo)](#ejemplo-de-uso-flujo-completo)
- [Ejecución de las pruebas](#ejecución-de-las-pruebas)
- [Herramientas de calidad y CI](#herramientas-de-calidad-y-ci)
- [Solución de problemas](#solución-de-problemas)
- [Decisiones técnicas](#decisiones-técnicas)

## ¿Qué es y qué resuelve?

Es una API para **administrar listas de tareas** y las tareas dentro de ellas. Su objetivo
es ofrecer un backend limpio y testeable que cubra:

- **CRUD** de listas y de tareas.
- **Cambio de estado** de una tarea (`pending` → `in_progress` → `done`).
- **Filtrado** de tareas por estado y prioridad.
- Un campo calculado de **porcentaje de completitud** por lista.
- Casos de uso que suman puntos: **autenticación JWT** (access + refresh con rotación y
  revocación), **colaboradores con roles** (`viewer`/`editor`/`owner`), **asignación de un
  usuario responsable** y **notificación por email** entregada vía **outbox** con reintentos
  (backend `log` simulado o `smtp` real).

El contrato completo y probador interactivo están en `http://localhost:8000/docs`
(Swagger UI) una vez levantada la aplicación.

### Alcance: núcleo del desafío vs. extras

El enunciado sugiere un **límite de tiempo** de 4-6 h y pide priorizar lo principal. Para que la
revisión sea transparente, esto es lo que es **núcleo** (lo exigido) y lo que son **extras**
de demostración añadidos deliberadamente por encima del mínimo:

| Capa | Qué incluye | Estado |
|------|-------------|--------|
| **Núcleo obligatorio** (§1.a, §2–6) | CRUD de listas y tareas, cambio de estado, listado con filtros estado/prioridad + % de completitud, capas limpias, Pydantic, excepciones propias, pytest (unit + integración), flake8/black, Docker + compose, README + DECISION_LOG | Completo |
| **Bonus del enunciado** (§1.b) | Login/JWT, asignación de responsable, notificación ficticia por email | Completo |
| **Extras propios** (fuera del límite de tiempo) | Refresh tokens con rotación y detección de reúso (ADR-13), RBAC por colaboradores (ADR-14), outbox transaccional con worker e idempotencia (ADR-15), límite de intentos por IP con backend Redis opcional (ADR-17), migraciones Alembic (ADR-6), purga de tokens (ADR-21), paginación keyset (ADR-12), suite E2E, escaneo Trivy y lockfile con `uv` (ADR-16/19) | Opcional |

> Los **extras** no son necesarios para cumplir el desafío; se incluyen para mostrar cómo
> evolucionaría hacia producción y están aislados detrás de **interruptores de configuración**
> o perfiles de compose, de modo que el núcleo funciona sin ellos. Cada uno está justificado en
> un ADR.

### Arquitectura por capas

```
web ──────────────┐
                  ├──► application ──► domain
infrastructure ───┘
```

| Capa | Contenido | Reglas |
|------|-----------|--------|
| `app/domain` | Entidades (Pydantic V2), enums, excepciones e **interfaces** (ABC) de repositorios, seguridad y notificador. | Cero dependencias de infraestructura. |
| `app/application` | Casos de uso (servicios) con la lógica de negocio. | Depende solo de abstracciones de `domain`. |
| `app/infrastructure` | Repositorios SQLAlchemy, sesión async, JWT, hash de contraseñas (bcrypt), notificador y `Settings`. | Implementa las interfaces de `domain`. |
| `app/web` | Routers FastAPI, esquemas de petición/respuesta, inyección de dependencias (`Depends`) y manejo de errores. | Cablea todo y traduce excepciones a HTTP. |

> **Para perfiles senior:** el porqué de cada decisión (cuatro capas en vez de tres,
> acceso por colaboradores con rol, alcance del JWT, `create_all` vs. migraciones,
> healthchecks para Kubernetes, etc.) está razonado en [`DECISION_LOG.md`](DECISION_LOG.md).

### Estructura del proyecto

```
app/
├── domain/           # Entidades, enums, excepciones e interfaces (puertos). Sin I/O.
├── application/      # Servicios = casos de uso. Orquestan el dominio.
├── infrastructure/   # Implementaciones: SQLAlchemy, JWT, bcrypt, config, notificador.
│   ├── db/           # Modelos ORM, sesión async y repositorios concretos.
│   └── security/     # Hash de contraseñas y emisión/verificación de JWT.
└── web/              # FastAPI: routers, esquemas HTTP, inyección de dependencias y errores.
tests/
├── unit/             # Servicios con dobles en memoria (sin BD).
├── integration/      # API completa contra SQLite async en memoria.
└── e2e/              # Contra un servidor uvicorn + PostgreSQL reales (opcionales).
migrations/           # Migraciones Alembic (env.py async + versiones).
alembic.ini           # Config de Alembic (lee DATABASE_URL del entorno).
docker_entrypoint.py  # Entrypoint Python (sin shell): migra y arranca uvicorn.
Dockerfile            # Imagen multistage, base fijada por digest, sin privilegios.
docker-compose.yml    # API + PostgreSQL (+ worker/redis opcionales por perfil).
pyproject.toml        # Metadatos y dependencias (rangos) + config de herramientas.
uv.lock               # Lockfile universal (fuente de verdad de versiones). Ver ADR-19.
requirements.txt      # Exportación fijada y con hashes de uv.lock (construcción de Docker).
```

> El worker del outbox de notificaciones corre dentro del `lifespan` por defecto, o como
> **proceso dedicado** (`python -m app.infrastructure.outbox_worker`) si se desactiva con
> `RUN_OUTBOX_WORKER_IN_PROCESS=false`
> ([app/infrastructure/outbox_worker.py](app/infrastructure/outbox_worker.py)). Ver **ADR-15**.

### Endpoints principales (prefijo `/api/v1`)

| Método | Ruta | Descripción | Auth |
|--------|------|-------------|------|
| GET | `/health` | Liveness (no toca la BD) | Público |
| GET | `/health/ready` | Readiness (verifica conexión a la BD) | Público |
| POST | `/auth/register` | Registro de usuario | Público |
| POST | `/auth/login` | Login → access + refresh token | Público |
| POST | `/auth/refresh` | Rota el refresh token → nuevo par | Público |
| POST | `/auth/logout` | Revoca un refresh token (esta sesión) | Público |
| POST | `/auth/logout-all` | Revoca todas las sesiones del usuario | JWT |
| GET/POST | `/lists` | Listar (keyset `?limit=&cursor=`) / crear listas | JWT |
| GET/PUT/DELETE | `/lists/{id}` | Obtener (con % completitud) / actualizar / eliminar | JWT |
| GET/POST/PUT/DELETE | `/lists/{id}/members[/{user_id}]` | Gestionar colaboradores y roles | JWT (owner) |
| GET/POST | `/lists/{id}/tasks` | Listar (filtros `?status=&priority=`, keyset `?limit=&cursor=` + % completitud) / crear | JWT |
| GET/PUT/DELETE | `/lists/{id}/tasks/{task_id}` | Obtener (con `ETag`) / actualizar / eliminar tarea | JWT (PUT/DELETE: `If-Match`) |
| PATCH | `/lists/{id}/tasks/{task_id}/status` | Cambiar estado | JWT (`If-Match`) |
| PATCH | `/lists/{id}/tasks/{task_id}/assignee` | Asignar responsable —debe ser miembro— (encola invitación) | JWT (`If-Match`) |

> **Autenticación:** el login devuelve un **access token** corto (15 min) y un **refresh
> token** de vida larga; renueva con `/auth/refresh` (rotación de un solo uso). Ver **ADR-13**.
> Un worker de mantenimiento **purga periódicamente** los refresh tokens expirados
> (sin tocar los vigentes, para preservar la detección de reúso). Ver **ADR-21**.
>
> **Acceso por rol:** cada lista tiene colaboradores con rol `viewer < editor < owner`. No
> ser miembro devuelve `404` (no se revela la existencia); rol insuficiente devuelve `403`.
> Ver **ADR-14**.
>
> **Paginación keyset:** los listados (listas, tareas y colaboradores) devuelven
> `{items|tasks|members, limit, next_cursor}`; pasa `next_cursor` como `?cursor=` para la
> página siguiente. Ver **ADR-12**.
>
> **Notificaciones:** asignar un responsable **encola** una invitación en un outbox que un
> worker entrega con reintentos e idempotencia (`NOTIFIER_BACKEND=log|smtp`). El responsable
> debe ser **miembro** de la lista (si no, `409`); así la invitación llega a quien sí puede
> verla. Ver **ADR-15** y **ADR-20**.
>
> **Concurrencia optimista (tareas):** cada tarea expone su versión en el campo `version` y en
> la cabecera **`ETag`**. Modificarla (`PUT`, `PATCH .../status`, `PATCH .../assignee`,
> `DELETE`) exige la precondición **`If-Match`** con ese ETag: si falta se responde `428`, y si
> quedó obsoleto (otra escritura subió la versión) `412` —evita la *actualización perdida*—. Usa
> `If-Match: *` para forzar la escritura sobre cualquier versión vigente. Ver **ADR-26**.
>
> ```bash
> # Lee la tarea y captura su ETag, reenvíalo en If-Match al escribir
> ETAG=$(curl -sD - -o /dev/null http://localhost:8000/api/v1/lists/1/tasks/1 \
>   -H "Authorization: Bearer <TOKEN>" | grep -i etag | cut -d' ' -f2 | tr -d '\r')
> curl -X PATCH http://localhost:8000/api/v1/lists/1/tasks/1/status \
>   -H "Authorization: Bearer <TOKEN>" -H "If-Match: $ETAG" \
>   -H "Content-Type: application/json" -d '{"status": "done"}'
> ```

## Prerrequisitos

Elige **una** de las dos vías de ejecución:

| Vía | Necesitas | Recomendada para |
|-----|-----------|------------------|
| **Docker** (más simple) | [Docker Desktop](https://www.docker.com/products/docker-desktop/) (incluye `docker compose`) | Cualquiera que solo quiera ejecutar la app. Levanta la API **y** PostgreSQL. |
| **Local** (sin Docker) | **Python 3.12+** y una instancia de **PostgreSQL** accesible | Desarrollo y depuración paso a paso. |

En ambos casos necesitas **git** para clonar el repositorio:

```bash
git clone <url-del-repositorio>
cd prueba_crehana
```

> En **Windows** puedes usar PowerShell o CMD; los comandos `git`, `docker` y `python`
> funcionan igual. Donde un comando difiera entre sistemas, abajo se indica la variante.

## Inicio rápido con Docker (recomendado)

Es la forma más rápida y la única que **no** requiere instalar Python ni PostgreSQL: el
`docker-compose` levanta la base de datos por ti.

**1. Crea tu archivo `.env`** a partir del ejemplo:

| Sistema | Comando |
|---------|---------|
| Linux / macOS | `cp .env.example .env` |
| Windows (PowerShell) | `Copy-Item .env.example .env` |
| Windows (CMD) | `copy .env.example .env` |

**2. Levanta todo** (API + PostgreSQL):

```bash
docker compose up --build
```

Cuando veas que el contenedor `api` está escuchando, abre:

- **API:** http://localhost:8000
- **Documentación interactiva (Swagger UI):** http://localhost:8000/docs

El esquema se aplica automáticamente al arrancar: el contenedor ejecuta
`alembic upgrade head` (migraciones versionadas) antes de lanzar la API, vía
[`docker_entrypoint.py`](docker_entrypoint.py) (ver [`DECISION_LOG.md`](DECISION_LOG.md),
**ADR-6** y **ADR-16**). Para detener: `Ctrl+C` y luego `docker compose down` (añade `-v`
si además quieres borrar los datos de PostgreSQL).

## Configuración del entorno local (sin Docker)

Útil para desarrollar con recarga en caliente y depurar. Requiere **Python 3.12+** y un
**PostgreSQL** accesible (puedes levantar solo la base con `docker compose up db`).

**1. Crea y activa un entorno virtual:**

| Sistema | Crear | Activar |
|---------|-------|---------|
| Linux / macOS | `python3 -m venv .venv` | `source .venv/bin/activate` |
| Windows (PowerShell) | `python -m venv .venv` | `.venv\Scripts\Activate.ps1` |
| Windows (CMD) | `python -m venv .venv` | `.venv\Scripts\activate.bat` |

> En Windows, si PowerShell bloquea el script de activación, ejecútalo una vez:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**2. Instala las dependencias** (runtime + herramientas de desarrollo):

La vía **canónica y reproducible** instala las versiones exactas del lock (incluido el
conjunto de herramientas de calidad: black/isort/flake8/ruff/mypy), idéntico al que usa CI (ver **ADR-23**):

```bash
uv sync --frozen --extra dev      # crea .venv con las versiones fijadas de uv.lock
```

Si prefieres `pip`, también funciona, con la salvedad de que **no fija** las versiones de las
herramientas de verificación (resuelve los rangos `>=` de `pyproject.toml`):

```bash
pip install -e ".[dev]"
```

**3. Crea tu `.env`** (mismo comando que en la sección de Docker, según tu sistema) y
asegúrate de que `DATABASE_URL` apunte a tu PostgreSQL.

**4. Aplica las migraciones** (crea el esquema; la app ya no lo hace al arrancar):

```bash
alembic upgrade head
```

**5. Arranca la API con recarga automática:**

```bash
uvicorn app.web.main:app --reload
```

Disponible en http://localhost:8000 (docs en `/docs`). Con Docker este paso es
automático (lo hace el entrypoint); en local se ejecuta una vez tras crear/actualizar
modelos.

## Variables de entorno

Se cargan desde el entorno o desde un archivo `.env` (ver `.env.example`).

| Variable | Obligatoria | Default | Descripción |
|----------|:-----------:|---------|-------------|
| `APP_ENV` | No | `dev` | Entorno de ejecución (`dev`/`development`/`local`/`test` o `production`). Fuera de desarrollo, el secreto de ejemplo de `.env.example` se **rechaza al arrancar**. Ver **ADR-5**. |
| `DATABASE_URL` | No | `postgresql+asyncpg://crehana:crehana@db:5432/crehana_tasks` | Conexión async a PostgreSQL. |
| `JWT_SECRET_KEY` | **Sí** | — | Clave de firma de los JWT. **Mínimo 32 caracteres** (RFC 7518 §3.2: ≥ 256 bits para HS256). Sin ella la app **no arranca**. Con `APP_ENV=production` se rechaza tanto el valor de ejemplo como cualquier secreto de **baja entropía** (< 112 bits estimados). Ver **ADR-25**. |
| `JWT_ALGORITHM` | No | `HS256` | Algoritmo de firma del token. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `15` | Minutos de validez del access token. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `7` | Días de validez del refresh token. |
| `LOGIN_RATE_LIMIT_MAX_ATTEMPTS` | No | `10` | Máx. intentos por IP y por ventana en los endpoints de `/auth/*` (`login`, `register`, `refresh` y `logout`/`logout-all`) (anti fuerza bruta y abuso). `0` deshabilita. Ver **ADR-17**. |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | No | `60` | Tamaño de la ventana deslizante del límite de login, en segundos. |
| `RATE_LIMITER_BACKEND` | No | `memory` | Contador del límite: `memory` (por réplica) o `redis` (compartido entre réplicas). Ver **ADR-17**. |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Instancia de Redis cuando `RATE_LIMITER_BACKEND=redis` (en compose: `redis://redis:6379/0`). |
| `RATE_LIMIT_TRUSTED_PROXIES` | No | _(vacío)_ | Lista de IP/CIDR de proxies de confianza cuyo `X-Forwarded-For` se honra al derivar la IP del cliente. Vacío = no confiar en `X-Forwarded-For` (usar IP del socket). Ver **ADR-17**. |
| `RUN_OUTBOX_WORKER_IN_PROCESS` | No | `true` | Lanza el worker del outbox dentro de la API. `false` para delegarlo a un proceso dedicado (perfil `worker` de compose). Ver **ADR-15**. |
| `RUN_TOKEN_CLEANUP_IN_PROCESS` | No | `true` | Ejecuta la purga periódica de refresh tokens expirados dentro de la API. `false` para delegarla a una tarea programada externa (la purga es idempotente). Ver **ADR-21**. |
| `TOKEN_CLEANUP_INTERVAL_SECONDS` | No | `3600` | Cadencia (segundos) del barrido de refresh tokens expirados. Ver **ADR-21**. |
| `NOTIFIER_BACKEND` | No | `log` | Backend de email: `log` (simulado) o `smtp` (real). |
| `LOG_LEVEL` | No | `INFO` | Nivel de log de la app (el envío simulado y la entrega del outbox se registran en INFO). |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_USE_TLS` | No | `localhost` / `25` / — / — / `no-reply@…` / `false` | Config SMTP cuando `NOTIFIER_BACKEND=smtp`. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | No | `crehana` / `crehana` / `crehana_tasks` | Credenciales del servicio `db` de docker-compose. |

> **Seguridad:** `JWT_SECRET_KEY` no tiene valor por defecto a propósito. El `.env.example`
> trae uno **solo para desarrollo**; en producción debe inyectarse desde un gestor de
> secretos (AWS/GCP/Azure, Kubernetes Secrets, etc.), nunca versionarse. Como red de
> seguridad, con `APP_ENV=production` la app **rechaza al arrancar** el secreto de ejemplo,
> de modo que ese valor conocido no pueda llegar a producción por descuido.

## Ejemplo de uso (flujo completo)

La forma más fácil de explorar la API es la **Swagger UI** en `/docs`: registra un usuario,
haz login, pulsa **Authorize** y pega el `access_token`. Si prefieres la terminal, este es
el flujo de extremo a extremo con `curl`:

```bash
# 1. Registrar un usuario
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@crehana.com", "password": "supersecret123"}'

# 2. Login → obtienes un access_token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "dev@crehana.com", "password": "supersecret123"}'
# Respuesta: {"access_token": "eyJ...", "refresh_token": "...", "token_type": "bearer"}

# 3. Usar el token (reemplaza <TOKEN>) para crear una lista
curl -X POST http://localhost:8000/api/v1/lists \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Sprint 1"}'

# 4. Crear una tarea dentro de la lista 1
curl -X POST http://localhost:8000/api/v1/lists/1/tasks \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Diseñar API", "priority": "high"}'

# 5. Listar tareas con el % de completitud (y filtros opcionales)
curl http://localhost:8000/api/v1/lists/1/tasks?status=done \
  -H "Authorization: Bearer <TOKEN>"
```

> En **Windows PowerShell**, `curl` es un alias de `Invoke-WebRequest`. Usa `curl.exe` con
> la misma sintaxis, o simplemente la Swagger UI en `/docs`.

## Ejecución de las pruebas

Las pruebas usan **SQLite async en memoria**, por lo que **no requieren PostgreSQL ni
Docker**. Tras instalar las dependencias de desarrollo (`pip install -e ".[dev]"`):

```bash
pytest                                  # toda la suite + cobertura (excluye E2E)
pytest tests/unit                       # solo unitarias (rápidas, con dobles)
pytest tests/integration                # solo integración (API en proceso)
pytest -k completion                    # filtra por nombre de test
```

El enunciado pide ≥ 75 %; `pytest.ini` fija el piso en **90 %** (`--cov-fail-under=90`) para
blindar el logro, e imprime el reporte línea a línea. La **cobertura medida actual es ≈ 98 %**
(muy por encima del mínimo);
cada corrida genera además `coverage.xml`, que en CI se publica como artefacto
(`coverage-report`, ver [`.github/workflows/ci.yml`](.github/workflows/ci.yml)). Para un
informe navegable en HTML:

```bash
pytest --cov-report=html      # genera htmlcov/index.html
```

Las pruebas unitarias y de integración usan **SQLite async en memoria**: rápidas,
deterministas y sin servidor.

> **Integración contra PostgreSQL real.** La misma suite de integración puede ejecutarse
> contra PostgreSQL (sin reescribir tests) definiendo `TEST_DATABASE_URL`, para validar los
> caminos que dependen del dialecto (el `INSERT ... ON CONFLICT` idempotente del outbox, los
> tipos `ENUM` y el esquema) en el mismo motor que producción:
>
> ```bash
> docker compose up db -d        # levanta solo PostgreSQL
> TEST_DATABASE_URL=postgresql+asyncpg://crehana:crehana@localhost:5432/crehana_tasks \
>   pytest tests/integration --no-cov
> ```
>
> En CI corre automáticamente en el job **`integration-postgres`** contra un PostgreSQL real
> (ver [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

### Pruebas end-to-end (despliegue real)

Las de integración llaman a la app **en proceso** (`ASGITransport`); validan el contrato,
pero no el software realmente desplegado. Las pruebas **E2E** ([tests/e2e/](tests/e2e/))
golpean un servidor `uvicorn` de verdad sobre HTTP real, contra **PostgreSQL real**, y
ejercitan el arranque vía `lifespan` y **toda la superficie de negocio**: ciclo de vida de
autenticación (registro, login, rotación de refresh con detección de reúso, logout y logout
global), CRUD completo de listas y tareas, filtros por estado/prioridad, completitud,
paginación por keyset y el modelo de **colaboradores con roles** (viewer/editor/owner, 403 y
404). La entrega real de la notificación de asignación se observa en el log del servidor.

> El límite por IP (`429`) se valida de forma determinista en la suite de **integración**,
> no en E2E: reproducirlo exigiría un umbral bajo que envenenaría la ventana por IP del
> servidor compartido. Por eso el entorno E2E arranca con `LOGIN_RATE_LIMIT_MAX_ATTEMPTS=0`.

Son **opcionales** (marcador `e2e`) y se excluyen de la corrida por defecto. Para ejecutarlas,
levanta el servidor y apunta a él:

```bash
docker compose up --build -d                          # API + PostgreSQL reales
pytest -m e2e --no-cov                                # corre contra http://localhost:8000
```

> Si lanzas el servidor a mano para E2E, exporta `LOGIN_RATE_LIMIT_MAX_ATTEMPTS=0` para que
> los numerosos `register`/`login` de la suite no choquen con el límite por IP.

Si el servidor no está accesible, las pruebas **fallan con un mensaje accionable** (no se
saltan en silencio). Configura otro destino con `E2E_BASE_URL`. En CI corren
automáticamente en un job dedicado contra un PostgreSQL real (ver
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Herramientas de calidad y CI

```bash
uv run black .        # Formateo
uv run isort .        # Orden de imports
uv run flake8         # Linter
uv run ruff check .   # Linter adicional
uv run mypy app       # Tipado estático estricto
uv run alembic check  # Verifica que las migraciones no divergen de los modelos
uv lock --check       # Verifica que uv.lock está al día con pyproject.toml (ver ADR-19)
```

Prefijar con `uv run` ejecuta cada herramienta con la versión **exacta del lock** (la misma
que CI), de modo que el veredicto es determinista. Si instalaste con `pip`, puedes omitir el
prefijo `uv run`, asumiendo que las versiones de tu entorno pueden diferir de las fijadas.

El formateo y los linters (`black`, `isort`, `flake8`, `ruff`) cubren **todo el repo** (`.`),
incluidas las migraciones de `migrations/` y la raíz —no solo `app tests`—, de modo que el
veredicto coincide con los comandos de arriba; `mypy` se acota a `app` (las migraciones
autogeneradas no son superficie tipada). Todas las herramientas excluyen `.venv` por defecto.
Ver **ADR-27**.

Estas mismas puertas (formateo, linters, tipado, `alembic check`, verificación del lockfile
y `pytest` con cobertura ≥ 90 %) se ejecutan automáticamente en cada subida y *pull request*
mediante GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), que instala
el entorno **desde el lock** (`uv sync --frozen`) para que las puertas corran contra el mismo
conjunto de herramientas con el que se verificó el código (ver **ADR-23**).

### Dependencias y lockfile (uv)

Las versiones se fijan con [uv](https://docs.astral.sh/uv/): `uv.lock` es la fuente de
verdad (lockfile universal) y `requirements.txt` es su exportación fijada y con hashes que
consume el `Dockerfile` para una construcción reproducible y verificada por hash (ver **ADR-19**).
Tras cambiar dependencias en `pyproject.toml`, regenera ambos:

```bash
uv lock                                                                    # actualiza uv.lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  -o requirements.txt                                                      # regenera requirements.txt
```

### Migraciones de base de datos

```bash
alembic upgrade head                      # aplica las migraciones pendientes
alembic downgrade -1                      # revierte la última
alembic revision --autogenerate -m "..."  # genera una nueva tras cambiar modelos
```

`alembic check` (en CI) falla si los modelos ORM y las migraciones divergen, evitando que
un cambio de esquema se quede sin migración. Ver [`DECISION_LOG.md`](DECISION_LOG.md),
**ADR-6**.

Al **generar** una migración, los `[post_write_hooks]` de `alembic.ini` la formatean
automáticamente con las mismas herramientas de calidad (isort → black → `ruff --fix`), de modo
que nace conforme a las puertas de todo el repo y no rompe `ruff check .` ni la CI. Ver **ADR-27**.

## Solución de problemas

| Síntoma | Causa probable | Solución |
|---------|----------------|----------|
| `ValidationError` / la app no arranca mencionando `jwt_secret_key` | Falta `JWT_SECRET_KEY`, tiene menos de 32 caracteres o (en `production`) es el de ejemplo o uno de baja entropía | Crea el `.env` (paso 1) o exporta una clave **aleatoria** de ≥ 32 caracteres: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Ver **ADR-25**. |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | El puerto 8000 (o 5432) ya está en uso | Cierra el proceso que lo ocupa o cambia el mapeo de puertos en `docker-compose.yml`. |
| `Cannot connect to the Docker daemon` | Docker Desktop no está en ejecución | Inicia Docker Desktop y reintenta. |
| `401 Unauthorized` en `/lists` o `/tasks` | Falta la cabecera `Authorization` o el token expiró | Repite login y envía `Authorization: Bearer <TOKEN>`. |
| En PowerShell, `.venv\Scripts\Activate.ps1` da error de permisos | Política de ejecución restringida | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| Local (sin Docker): error de conexión a la BD al arrancar | No hay PostgreSQL en `DATABASE_URL` | Levanta solo la base con `docker compose up db` o ajusta `DATABASE_URL`. |

## Decisiones técnicas

Las decisiones de diseño no especificadas por el enunciado (acceso por colaboradores con rol,
alcance del JWT, gestión del esquema, secreto obligatorio, etc.) se documentan como ADRs en
[`DECISION_LOG.md`](DECISION_LOG.md).

La **hoja de ruta a producción** que excede el **límite de tiempo** del desafío —y que se
documenta en lugar de implementarse, para no sobre-dimensionar la entrega— está acotada en ADRs
propios: **ADR-28** (persistencia políglota / NoSQL: almacén de documentos MongoDB con PyMongo
Async y caché Redis, detrás de puertos) y **ADR-29** (despliegue en GKE con Kubernetes/Kustomize,
IaC con Terraform y despliegue continuo sin claves por Workload Identity Federation).
