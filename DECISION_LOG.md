# DECISION_LOG — Registro de decisiones técnicas (ADR)

Decisiones de diseño en los puntos donde el enunciado del desafío deja libertad o no
especifica. La **fuente de verdad** del alcance es ese enunciado; cada registro describe la
decisión **vigente** en el código y su porqué.

---

## ADR-1 — Arquitectura: cuatro capas (arquitectura limpia)

**Contexto.** El PDF (§2.a) pide "estructura limpia por capas (Domain, Application,
Infrastructure)".

**Decisión.** Cuatro capas: `domain`, `application`, `infrastructure` y `web`. La cuarta
(`web`) aísla FastAPI (routers, inyección de dependencias, esquemas HTTP) de la lógica de
negocio.

**Consecuencias.** Las dependencias apuntan hacia el dominio (inversión de dependencias).
`domain` no importa SQLAlchemy ni FastAPI; los casos de uso se prueban con dobles sin tocar
la base de datos.

---

## ADR-2 — Base de datos: PostgreSQL + SQLAlchemy 2.0 asíncrono (`asyncpg`)

**Contexto.** El PDF (§Requisitos) pide "una base de datos real de tu preferencia".

**Decisión.** PostgreSQL con SQLAlchemy 2.0 en modo asíncrono (`asyncpg`), levantada en
Docker. En los tests se usa SQLite asíncrono (`aiosqlite`) en memoria.

**Consecuencias.** Pila asíncrona coherente con FastAPI. Se evitan tipos propios de
PostgreSQL en los modelos para mantener la portabilidad con SQLite en los tests.

---

## ADR-3 — Propiedad y acceso de las listas

**Contexto.** El PDF (§1.a) no menciona propiedad de las listas. Los usuarios solo aparecen
en los bonus: autenticación (§1.b.ii) y asignación de tareas (§1.b.iii). Un servicio
multiusuario no puede exponer las listas como recursos globales (filtraría datos entre
usuarios).

**Decisión.** Cada lista tiene un creador (`task_lists.owner_id`) y, por encima, un modelo de
**colaboradores con rol** (`list_members`): la autorización es "¿eres miembro con rol
suficiente?". El mecanismo de roles se detalla en **ADR-14** y la regla de asignación, en
**ADR-20**. No ser miembro hace la lista indistinguible de inexistente (**404**); serlo con
rol insuficiente devuelve **403**.

---

## ADR-4 — Acceso de usuarios: registro + login con bcrypt

**Contexto.** El bonus §1.b.ii pide "login y autenticación con JWT". No define cómo se crean
los usuarios.

**Decisión.** Endpoints `POST /auth/register` y `POST /auth/login`. Las contraseñas se
almacenan con hash **bcrypt**, usando la librería `bcrypt` directamente (sin passlib). El
límite de 72 bytes que admite el algoritmo se hace cumplir en la validación de entrada (ver
**ADR-22**).

**Consecuencias.** Flujo de autenticación autocontenido y verificable de extremo a extremo en
los tests. El registro crea usuarios reales que sirven de responsables en la asignación de
tareas.

---

## ADR-5 — Alcance del JWT: todo protegido salvo `/auth/*` y `/health`

**Contexto.** §1.b.ii habla de "proteger endpoints".

**Decisión.** Todos los endpoints de `/lists` y `/lists/{id}/tasks` exigen un Bearer JWT
válido. Son públicos únicamente el healthcheck y los endpoints de autenticación.

**Consecuencias.** La protección se aplica a nivel de router (`dependencies=[Depends(
get_current_user)]`), sin repetir la dependencia en cada operación.

**Secreto de firma.** `JWT_SECRET_KEY` es **obligatorio y sin valor por defecto** (mínimo 32
caracteres); si falta, la app **falla al arrancar** en lugar de firmar tokens con un secreto
conocido. Para no acoplar el arranque a la configuración, `Settings` y el motor de base de
datos se construyen de forma perezosa (no al importar el módulo), de modo que los tests
inyectan un secreto de prueba sin necesidad de un `.env`.

**Protección del secreto de ejemplo.** El `.env.example` versiona un secreto de conveniencia
para el arranque local en dos comandos. Para que ese valor **conocido y público** no llegue a
producción por descuido, `Settings` incorpora la variable `APP_ENV` (default `dev`) y un
validador que **impide arrancar** si `APP_ENV` no es de desarrollo
(`dev`/`development`/`local`/`test`) y el `JWT_SECRET_KEY` es uno de los secretos de ejemplo
conocidos. El despliegue productivo fija `APP_ENV=production`. Cubierto por
`tests/unit/test_config.py`.

---

## ADR-6 — Esquema de BD: migraciones versionadas con Alembic

**Contexto.** El PDF no pide migraciones, pero crear el esquema al arrancar no versiona los
cambios ni contempla evoluciones destructivas.

**Decisión.** El esquema se gestiona con **Alembic** (`migrations/`, motor asíncrono vía
`async_engine_from_config` + `connection.run_sync`). La app **no** crea tablas al arrancar: el
contenedor ejecuta `alembic upgrade head` desde `docker_entrypoint.py` (entrypoint en Python,
sin shell; ver **ADR-16**) antes de lanzar `uvicorn`, y en local se corre el mismo comando una
vez.

**Independencia de la configuración.** `migrations/env.py` lee `DATABASE_URL` directamente del
entorno (no del `Settings` de la app), de modo que las migraciones no dependen de otros
secretos como `JWT_SECRET_KEY`.

**Verificación.** La CI ejecuta `alembic upgrade head` + `alembic check` (detecta divergencia
entre los modelos y las migraciones) en cada subida. Los tests usan `create_all` sobre SQLite
en memoria: no necesitan migraciones y se mantienen rápidos.

**Consecuencias.** Cambios de esquema trazables y reversibles (`upgrade`/`downgrade`).
`alembic` es dependencia de ejecución (se usa en el arranque del contenedor).

---

## ADR-7 — Porcentaje de completitud: valor calculado, no persistido

**Contexto.** §1.a.iv pide "un campo extra donde se indique el porcentaje de completitud".

**Decisión.** Se calcula en `application` como `tareas DONE / total` de la lista (redondeado a
2 decimales); una lista sin tareas devuelve `0.0`. No se persiste. El conteo se resuelve con
una **agregación en la base de datos** (`ITaskRepository.completion_stats`, un `COUNT`
condicional) en vez de materializar todas las tareas en memoria.

**Consecuencias.** El valor siempre refleja el estado actual (sin inconsistencias) y se evita
la división por cero. Se expone en `GET /lists/{id}` y en el listado de tareas.

---

## ADR-8 — Transiciones de estado: libres

**Contexto.** §1.a.iii pide "cambiar el estado de una tarea" sin definir transiciones válidas.

**Decisión.** Se permite cualquier transición entre los estados del enum
(`pending`/`in_progress`/`done`). No se implementa una máquina de estados restrictiva.

**Consecuencias.** Menor complejidad, documentada como decisión consciente. Si el negocio lo
requiriera, la validación se añadiría en `TaskService.change_status`.

---

## ADR-9 — Notificación de asignación: puerto `INotifier` (log o SMTP)

**Contexto.** El bonus §1.b.iv pide "simulación de envío de invitación a usuarios por email
(no real)".

**Decisión.** El puerto `INotifier` vive en `domain`, con dos implementaciones en
`infrastructure` seleccionables por configuración (`NOTIFIER_BACKEND`): `LoggingEmailNotifier`
(simulado, registra en el log) y `SmtpEmailNotifier` (envío real por SMTP, ejecutado en un
hilo con `asyncio.to_thread` para no bloquear el bucle de eventos). Por defecto, `log`.

**Cuándo se dispara.** La invitación se emite siempre que se establece un **responsable
nuevo**: al crear una tarea con `assignee_id`, al cambiarlo en el `PUT` o desde el endpoint
`PATCH .../assignee`. La decisión de "a quién notificar" vive en `TaskService` (`create`,
`update` y `assign` devuelven el `User` a invitar o `None`); en el `PUT` se compara el
responsable anterior con el nuevo para no notificar dos veces al mismo. `change_status` nunca
notifica.

**Entrega.** La capa `web` **encola** la invitación en un outbox transaccional y un worker la
entrega con reintentos e idempotencia (ver **ADR-15**), de modo que no se pierde si el proceso
cae.

---

## ADR-10 — Healthcheck: liveness sin dependencias + readiness con chequeo de BD

**Contexto.** El healthcheck debe servir tanto para "el proceso está vivo" como para "el
servicio puede atender tráfico" (lo segundo depende de la base de datos).

**Decisión.** Dos sondas: `GET /health` es **liveness** (responde siempre, sin tocar
dependencias) y `GET /health/ready` es **readiness** (ejecuta `SELECT 1` contra la base de
datos; devuelve 503 si no es accesible). Ambas son públicas.

**Razonamiento.** Una sonda de liveness no debe depender de sistemas externos: un corte
transitorio de la base de datos no debe provocar el reinicio del contenedor. La verificación
de dependencias corresponde a readiness, que retira temporalmente la instancia del balanceador
sin matar el proceso.

**Consecuencias.** Compatible con orquestadores (Kubernetes `livenessProbe` /
`readinessProbe`). El test de liveness es determinista (no requiere base de datos).

---

## ADR-11 — Pruebas E2E contra el despliegue real (opcionales, fallan si no hay servidor)

**Contexto.** Los tests de integración llaman a la app en proceso (`ASGITransport`): validan
el contrato pero no el software realmente desplegado (servidor `uvicorn`, HTTP real,
PostgreSQL real, arranque vía `lifespan`).

**Decisión.** Una suite **E2E** (`tests/e2e/`) golpea un servidor real sobre HTTP contra
PostgreSQL real. Es **opcional** (marcador `e2e`, excluida por defecto con `-m "not e2e"`)
para no ralentizar ni acoplar a Docker el suite rápido y hermético. En la CI corre en un job
dedicado que levanta PostgreSQL y `uvicorn`, espera la readiness y ejecuta
`pytest -m e2e --no-cov`.

**Fallo, no salto.** Si el servidor no está accesible, las pruebas **fallan con un mensaje
accionable**, no se saltan. Un salto aparecería como CI en verde y haría creer que la
validación corrió cuando no tocó nada. Como la suite por defecto ya las excluye, invocarlas
explícitamente significa "valida el despliegue": sin servidor, eso es un fallo legítimo.

**Alcance.** La suite E2E cubre **toda la superficie de negocio**, no solo una prueba de humo:
healthchecks (liveness/readiness real), ciclo de vida de autenticación (registro, login,
rotación de refresh con detección de reúso, logout y logout global), CRUD completo de listas y
tareas, filtros por estado/prioridad, completitud, paginación keyset, colaboradores con roles
(viewer/editor/owner, 403/404) y la entrega de la notificación de asignación (verificada en el
log del servidor). El límite por IP (`429`) se valida en **integración**, y el entorno E2E
arranca con `LOGIN_RATE_LIMIT_MAX_ATTEMPTS=0` (un umbral bajo envenenaría la ventana por IP del
servidor compartido y rompería el aislamiento entre pruebas).

**Consecuencias.** Cubre lo que el suite en proceso no puede (servidor real, red, PostgreSQL,
`lifespan`). No se mide cobertura sobre ellas (`--no-cov`): el servidor corre en otro proceso y
su valor es la validación de extremo a extremo, no la métrica de líneas.

---

## ADR-12 — Paginación de los listados: keyset (cursor)

**Contexto.** Los listados (`GET /lists` y `GET /lists/{id}/tasks`) deben acotar las filas
devueltas. El esquema `offset`/`limit` degrada en páginas profundas (escanea y descarta) y es
inestable ante inserciones concurrentes.

**Decisión.** Paginación por **keyset**: `limit` (default 50, `1..100`) y `cursor` (id del
último elemento de la página previa). La consulta filtra `WHERE id > :cursor ORDER BY id LIMIT
n`, que es O(limit) y aprovecha el índice por `id`. La respuesta es un envoltorio con
`items`/`tasks`, `limit` y `next_cursor` (`null` cuando no hay más). No se devuelve `total`:
contar exigiría un `COUNT` que escanea, lo que contradice el objetivo del keyset.
`completion_percentage` sigue calculándose sobre **todas** las tareas de la lista.

**Detección de página siguiente.** Se piden **`limit + 1`** filas: si vuelven más de `limit`,
hay otra página y se emite `next_cursor`; si no, `next_cursor` es `null`. El elemento sobrante
no se devuelve. Así se evita el caso en que un total múltiplo exacto de `limit` emitía un
cursor que llevaba a una página final **vacía**. Es el patrón estándar de keyset (pedir n+1),
sin `COUNT` adicional.

**Contrapartida.** No permite saltar a una página arbitraria; a cambio, escala y es estable.

---

## ADR-13 — Sesiones: access token corto + refresh con rotación y revocación

**Contexto.** El bonus §1.b.ii pide JWT para proteger endpoints. Un único access token de
larga duración no permite cerrar sesión ni invalidar credenciales filtradas (OWASP).

**Decisión.** Dos credenciales: un **access token** JWT corto (15 min, sin estado, con los
campos `sub`, `ver`, `jti`, `type`) y un **refresh token** opaco de vida larga (7 días) del que
se persiste solo su hash SHA-256. `POST /auth/refresh` aplica **rotación de un solo uso**
(revoca el anterior, emite uno nuevo) con **detección de reutilización**: si llega un refresh
ya revocado, se revoca toda la sesión del usuario. `POST /auth/logout` revoca un refresh
concreto; `POST /auth/logout-all` incrementa `users.token_version` (invalida todos los access
vigentes, comparados en `get_current_user`) y revoca todos los refresh.

**Sin Redis.** La lista de refresh tokens revocados vive en PostgreSQL (`refresh_tokens`) y la
invalidación masiva de access usa `token_version`, sin introducir Redis. Con muchas réplicas,
una lista de denegación de access por `jti` en Redis sería el siguiente paso.

---

## ADR-14 — Autorización: colaboradores con rol (`list_members`)

**Contexto.** Un responsable asignado debe poder ver su tarea, así que la colaboración exige
compartir el acceso a la lista, no restringirla a un único dueño.

**Decisión.** Tabla `list_members (list_id, user_id, role)` con roles ordenados
`viewer < editor < owner`. Las lecturas exigen `viewer`; las escrituras de tareas y la edición
de la lista, `editor`; borrar la lista y gestionar miembros, `owner`. Quien crea la lista queda
como `owner`. La autorización vive en los servicios (`_require_role`). No ser miembro → **404**
(no se filtra existencia); serlo con rol insuficiente → **403**.

**Anclaje del creador.** No se puede degradar ni expulsar al `owner_id` original, para no dejar
una lista huérfana. El control de acceso es por recurso; un RBAC global (roles a nivel de
organización) queda como evolución.

---

## ADR-15 — Entrega de notificaciones: outbox transaccional + worker

**Contexto.** La entrega de la invitación debe sobrevivir a una caída del proceso o a un fallo
del proveedor, con reintento y traza; una entrega en segundo plano dentro del propio proceso se
perdería en esos casos.

**Decisión.** Patrón **outbox transaccional**: al fijar un responsable, el mensaje se inserta
en `notification_outbox` con una **clave de idempotencia** (`task_assignment:{task}:{user}`)
**dentro de la misma transacción** que la escritura de negocio. Esto se logra con una **unidad
de trabajo por request**: los repositorios solo hacen `flush` y el commit único lo realiza
`get_session` al terminar el endpoint (rollback si lanza un error inesperado; ver
`app/infrastructure/db/session.py`). Así, o se persisten **la tarea y la invitación juntas, o
ninguna**. La idempotencia se resuelve con un `INSERT ... ON CONFLICT DO NOTHING` **atómico**
sobre el índice único de `idempotency_key` (con `RETURNING` para saber si se insertó o se
omitió): un único statement, no un `SELECT` seguido de `INSERT`. Así, dos requests concurrentes
con la misma clave no provocan un `IntegrityError` que aborte la transacción de negocio (una
inserta, la otra se omite). No se usan `SAVEPOINT` (en SQLite con SQLAlchemy 2.0 no participan
de forma fiable); con `ON CONFLICT` son innecesarios porque el conflicto no genera excepción.
PostgreSQL y SQLite (3.35+, usado en tests) soportan la cláusula; el constructo se selecciona
por dialecto en `SqlAlchemyOutboxRepository.enqueue`, que **valida el dialecto de forma
explícita y falla rápido** (`RuntimeError`) ante un motor no soportado, en lugar de asumir
SQLite en silencio. Es el mismo criterio que el `JWT_SECRET_KEY` obligatorio (ADR-5): un error
de configuración se hace ruidoso en vez de degradar a un comportamiento incorrecto.

**Entrega segura con varias réplicas.** Un **worker** en proceso (lanzado en el `lifespan`) lee
los `pending` con `SELECT ... FOR UPDATE SKIP LOCKED` (sin efecto en SQLite) y los entrega vía
`INotifier`, con **reintentos** (hasta `MAX_ATTEMPTS`, luego `failed`). El worker es su propia
unidad de trabajo: **un commit por tanda**, que mantiene el bloqueo de las filas tomadas hasta
persistir los marcados, de modo que **varias réplicas del worker no entregan el mismo
mensaje**. Garantiza entrega **al menos una vez** sin invitaciones repetidas. La emisión de la
cláusula de bloqueo se verifica de forma determinista compilando la consulta contra el dialecto
PostgreSQL en `tests/integration/test_outbox_delivery.py` (sin una base de datos PostgreSQL
viva en el suite rápido).

**Modo de ejecución del worker.** Según `RUN_OUTBOX_WORKER_IN_PROCESS` (default `true`):

- **En proceso**, lanzado en el `lifespan` de la API. Suficiente para 1..pocas réplicas.
- **Como proceso dedicado**: `python -m app.infrastructure.outbox_worker` (con manejo de
  SIGINT/SIGTERM para drenar la tanda en curso y cerrar limpiamente). En `docker-compose` es el
  servicio opcional `worker` (perfil `worker`). Recomendado con muchas réplicas: se pone
  `RUN_OUTBOX_WORKER_IN_PROCESS=false` para que cada réplica de la API no repita el sondeo de la
  tabla (lo que multiplicaría las consultas y la contención de bloqueos).

Gracias a la toma con `SELECT ... FOR UPDATE SKIP LOCKED`, cualquier combinación de workers (en
proceso y/o dedicados) entrega cada mensaje **una sola vez**. Mover la entrega a una cola
dedicada (Celery/arq) es una evolución opcional, no necesaria para escalar de forma segura.

---

## ADR-16 — Imagen y cadena de suministro: digest, parches, entrypoint Python, Trivy

**Contexto.** La base `python:3.12-slim` sin fijar arrastra CVEs del SO y, sin pin por digest,
es vulnerable a un re-etiquetado del registro.

**Decisión.** (1) Base **fijada por digest**; (2) **parches de seguridad** del SO en la capa de
ejecución (`apt-get upgrade`); (3) **entrypoint en Python** (`docker_entrypoint.py`, sin
`/bin/sh`) que aplica migraciones y ejecuta uvicorn —elimina la dependencia de shell y deja el
camino abierto a distroless—; (4) **escaneo Trivy** en la CI que falla en HIGH/CRITICAL
corregibles; (5) endurecimiento de ejecución en compose (`read_only`, `cap_drop: ALL`,
`no-new-privileges`, `tmpfs /tmp`) además del usuario sin privilegios.

**Distroless: pendiente concreto.** No se usa distroless porque la variante
`distroless/python3-debian12` ejecuta Python **3.11** y el proyecto requiere **3.12** (el venv
no sería compatible). Con el entrypoint ya sin shell, el cambio es un reemplazo de base en
cuanto exista una distroless 3.12 (o usando Chainguard).

---

## ADR-17 — Límite de intentos en autenticación (anti fuerza bruta)

**Contexto.** Los endpoints de autenticación deben acotar el número de intentos: sin límite, un
atacante puede probar miles de credenciales por segundo (fuerza bruta) o dar de alta cuentas en
masa. La vacante enfatiza la seguridad.

**Decisión.** Un puerto `ILoginRateLimiter` (en `domain`) con una implementación de **ventana
deslizante** en memoria (`InMemorySlidingWindowRateLimiter`, en `infrastructure`). Se aplica
**por IP** en login y registro (claves separadas), **antes** de validar credenciales, y al
superar el umbral responde **429** con cabecera **`Retry-After`**.

**Cobertura de endpoints.** El límite cubre **todos** los `/auth/*`: los de credenciales
(`login`, `register`, `refresh`) frenan la fuerza bruta y el barrido de tokens; `logout` y
`logout-all` se limitan también (clave `"logout"`) por consistencia y defensa en profundidad
—son endpoints que disparan una escritura en base de datos y `logout` es público, así que
acotar su abuso es prudente y barato—. El umbral y la ventana son configurables
(`LOGIN_RATE_LIMIT_MAX_ATTEMPTS`, default 10; `LOGIN_RATE_LIMIT_WINDOW_SECONDS`, default 60);
`0` lo deshabilita. Se usa `time.monotonic` (inmune a saltos del reloj) y un reloj inyectable
para los tests.

**Alcance / contrapartida.** Se prefiere el límite por ventana al **bloqueo de cuenta** para no
habilitar una denegación de servicio por bloqueo dirigido a un usuario (guía OWASP/NIST). El
limitador vive en `app.state` (no como singleton de módulo) para que cada instancia —incluida
cada app de test— tenga su estado aislado.

**Backend seleccionable (memoria o Redis).** El puerto tiene dos implementaciones elegibles por
`RATE_LIMITER_BACKEND`: `InMemorySlidingWindowRateLimiter` (default, estado por proceso → límite
**por réplica**) y `RedisSlidingWindowRateLimiter` (estado en Redis → límite **global** entre
réplicas). La variante Redis usa un conjunto ordenado por clave (puntuación = marca de tiempo) y
aplica, en una transacción `MULTI/EXEC` atómica, `ZREMRANGEBYSCORE` (purga la ventana) + `ZADD`
+ `ZCARD` + `EXPIRE`; el intento bloqueado no se cuenta (`ZREM`), igual que la variante en
memoria. Usa reloj de pared (las marcas deben ser comparables entre procesos). La selección no
toca la capa web: ambas viven en `app.state.login_rate_limiter`. En `docker-compose` el servicio
`redis` es opcional (perfil `redis`).

**IP del cliente resistente a suplantación.** `X-Forwarded-For` es falsificable, así que solo se
honra cuando el peer directo es un **proxy de confianza** (`RATE_LIMIT_TRUSTED_PROXIES`, lista
de IP/CIDR); en otro caso se usa la IP del socket. Al confiar, se descartan los saltos de
confianza por la derecha y se toma la primera IP no confiable como cliente real
(`app/web/client_ip.py`). Vacío por defecto = no confiar en `X-Forwarded-For` (seguro de
fábrica).

---

## ADR-18 — Unidad de trabajo: persistir efectos al rechazar es opcional (default seguro)

**Contexto.** La unidad de trabajo por request (`get_session`, ADR-15) debe decidir qué hacer
ante un `DomainError`. Confirmar siempre dependería de una invariante frágil ("ningún caso de
uso debe escribir antes de lanzar un 4xx"): un caso de uso que escribiera y luego lanzara un
error de validación filtraría esa escritura sin que nadie lo notara.

**Decisión.** El `DomainError` lleva una bandera `commit_side_effects` (default **`False`**). La
unidad de trabajo, ante un `DomainError`, **revierte por defecto** y solo confirma si la
excepción la activa explícitamente. El único caso que la usa es la revocación de sesión por
reúso de refresh (`AuthError(..., commit_side_effects=True)`). Persistir al rechazar es una
**decisión explícita y local** del caso de uso, no la conducta por defecto.

**Consecuencias.** Default seguro: añadir un nuevo rechazo de validación no puede filtrar
escrituras por accidente. Cubierto por `tests/unit/test_unit_of_work.py` (rollback por defecto;
commit solo con la bandera).

---

## ADR-19 — Dependencias reproducibles: lockfile con uv + requirements.txt con hashes

**Contexto.** Para que dos construcciones de la imagen en momentos distintos resuelvan las
mismas versiones (reproducibilidad) y para verificar la integridad de las wheels descargadas
(cadena de suministro, en la línea del pin por digest y el escaneo Trivy de ADR-16), las
dependencias deben quedar fijadas, no solo declaradas con rangos (`>=`).

**Decisión.** Se adopta **uv** como gestor de lock (lockfile **universal**, independiente de
plataforma, que evita resolver en el SO de desarrollo para un contenedor distinto). `uv.lock`
es la fuente de verdad. Para la construcción de Docker —que sigue usando pip— se exporta un
`requirements.txt` **fijado y con hashes** (`uv export --frozen --no-dev --no-emit-project`), y
el `Dockerfile` instala con `pip install --require-hashes --no-deps -r requirements.txt` (falla
si una wheel no coincide con su hash) y luego el proyecto con `--no-deps`.

**Verificación.** La CI ejecuta `uv lock --check` (el lock está al día con `pyproject.toml`) y
re-exporta `requirements.txt` comparándolo con el versionado (`git diff --exit-code`), de modo
que un cambio de dependencias sin regenerar el lock falla la construcción. Es el mismo criterio
sin-divergencia que `alembic check` (ADR-6).

**Consecuencias.** Construcciones deterministas y verificadas por hash sin cambiar el gestor de
ejecución (pip). `pyproject.toml` mantiene los rangos como entrada de resolución; `uv.lock` y
`requirements.txt` fijan la salida.

---

## ADR-20 — Asignación de tareas: el responsable debe ser miembro de la lista

**Contexto.** El bonus §1.b.iii pide asignar un responsable y §1.b.iv simular el envío de una
**invitación** por email. Permitir asignar a cualquier usuario registrado tendría dos
problemas: (1) la "invitación" se enviaría a alguien **sin acceso** a la lista (incoherente);
(2) como crear tareas/asignar requiere rol `editor` pero **gestionar miembros requiere `owner`**
(ADR-14), asignar a externos dejaría que un `editor` concediera acceso de hecho —una **escalada
de privilegios** que sortea el control de pertenencia del `owner`.

**Decisión.** Una tarea solo puede asignarse a un **miembro** de la lista (cualquier rol;
`viewer` basta, igual que en GitHub/Jira el asignado necesita acceso de lectura). Asignar a un
usuario inexistente devuelve **404**; asignar a un usuario que existe pero **no es miembro**
devuelve **409** con un mensaje accionable ("añádelo como colaborador antes de asignarle
tareas"). La comprobación vive en `TaskService._ensure_assignee` y cubre los tres caminos que
fijan responsable: `create`, `update` (PUT) y `assign` (PATCH .../assignee).

**Consecuencias.** La pertenencia es la **única fuente de verdad del acceso**, controlada por el
`owner`; asignar **no** concede acceso por sí mismo. Para asignar a alguien externo, el `owner`
lo añade antes como colaborador (un paso explícito y auditable). La invitación por email queda
dirigida siempre a quien sí puede ver la lista.

**Coherencia al expulsar a un colaborador.** La invariante "el responsable es miembro" se valida
al asignar y también se **mantiene** cuando un miembro deja de serlo. Al expulsar a un
colaborador (`remove_member`), las tareas de esa lista que tenía asignadas se **desasignan**
(`assignee_id → NULL`) en la **misma unidad de trabajo** que la expulsión
(`ITaskRepository.clear_assignee_in_list`, un único `UPDATE`). Así no queda un no-miembro como
responsable ni se le reenvían invitaciones. Es el equivalente, a nivel de aplicación, al
`ON DELETE SET NULL` del FK `assignee_id` cuando se elimina el usuario por completo; se prefiere
desasignar (no reasignar), la conducta menos sorprendente y alineada con GitHub/Jira. Solo
afecta a las tareas de la lista de la que se expulsa: si el usuario sigue en otras listas, sus
tareas allí no se tocan. Cubierto por `tests/unit/test_task_list_service.py` y
`tests/integration/test_tasks_api.py`.

---

## ADR-21 — Higiene de refresh tokens: purga periódica de los expirados

**Contexto.** La tabla `refresh_tokens` (ADR-13) crece con cada login y cada rotación de
refresh. Sin limpieza, acumula tokens vencidos indefinidamente: más almacenamiento e índices y
una superficie innecesaria de datos sensibles (aunque solo se guarde el hash) en reposo. RFC
9700 (BCP de seguridad OAuth 2.0, enero 2025) y la guía OWASP recomiendan automatizar la
eliminación de tokens expirados con un **trabajo en segundo plano** (fuera de la ruta de la
petición, para no añadir latencia ni contención a la ruta de autenticación).

**Decisión.** El repositorio ofrece `delete_expired(now)` —un único
`DELETE FROM refresh_tokens WHERE expires_at < :now`— y un worker de mantenimiento
(`app/infrastructure/maintenance.py`) lo invoca periódicamente. Corre **dentro del proceso de
la API**, lanzado en el `lifespan` (junto al worker del outbox), con su propia unidad de trabajo
(sesión + commit por pasada). Es configurable: `RUN_TOKEN_CLEANUP_IN_PROCESS` (default `true`) y
`TOKEN_CLEANUP_INTERVAL_SECONDS` (default `3600`).

**Solo se purga lo ya vencido.** El predicado es `expires_at < now`, de modo que un refresh
**vigente** —o uno **revocado pero aún dentro de su ventana**— nunca se borra. Así se preserva
intacta la **detección de reúso** de ADR-13: un token revocado se conserva hasta expirar para
poder reconocer su reutilización; una vez vencido ya no es utilizable, así que eliminarlo no
abre ningún hueco de seguridad.

**Idempotencia y varias réplicas.** La purga es idempotente (un `DELETE` por rango): correrla en
varias réplicas es inofensivo (la segunda no encuentra nada). Con muchas réplicas puede
desactivarse en proceso (`RUN_TOKEN_CLEANUP_IN_PROCESS=false`) y delegarse a una tarea
programada externa (cron / `CronJob` de Kubernetes), igual que el worker del outbox (ADR-15).

**Sin índice sobre `expires_at` (deliberado).** No se añade un índice por `expires_at`: el
propósito del barrido es mantener la tabla pequeña, con lo que el `DELETE` recorre pocas filas;
un índice solo añadiría coste de escritura en cada emisión de token sin beneficio neto. Si el
volumen lo exigiera, es un cambio aditivo (una migración con el índice), no un rediseño.

**Consecuencias.** Crecimiento de `refresh_tokens` acotado al conjunto de sesiones activas. Sin
esquema nuevo (usa la columna `expires_at` existente), así que `alembic check` no cambia.
Cubierto por `tests/unit/test_maintenance.py` (bucle y predicado con dobles),
`tests/integration/test_token_cleanup.py` (repositorio real + unidad de trabajo) y
`tests/unit/test_lifespan.py` (arranque condicional del worker).

---

## ADR-22 — Contraseñas: rechazar las que exceden los 72 bytes de bcrypt (no truncar)

**Contexto.** El hash de contraseñas usa **bcrypt** (ADR-4), cuyo algoritmo solo procesa los
primeros **72 bytes** del input; el resto se ignora. Truncar en silencio es un riesgo: una
contraseña que difiere solo más allá del byte 72 produciría el **mismo hash** (degrada la fuerza
efectiva y confunde al usuario).

**Decisión.** Se hace cumplir el límite en el **borde de validación**: `RegisterRequest` rechaza
con **422** toda contraseña cuya codificación UTF-8 supere los 72 bytes (`field_validator`,
límite inclusivo), en lugar de truncar en silencio. Es la recomendación **primaria** de la OWASP
*Password Storage Cheat Sheet* ("enforce a maximum password length of 72 bytes"). El recorte del
hasher se conserva como **salvaguarda defensiva** (el primitivo no confía en que el llamador haya
validado), pero no es el control primario.

**Alternativa descartada: pre-hashear.** Permitir contraseñas arbitrariamente largas
pre-hasheando con SHA-256 y `bcrypt(base64(...))` se descartó: sin un *pepper* en un HMAC
(almacenado fuera de la base de datos) habilita *password shucking*
(`bcrypt(base64(H(pw))) == bcrypt(base64(hash_filtrado))`), y añadir gestión de *pepper* es una
complejidad desproporcionada para este caso. Con un tope de 72 bytes —una frase de paso larga
sigue cabiendo— el truncado desaparece sin introducir nuevas superficies. Si en el futuro se
exigieran contraseñas más largas, la evolución correcta es
`bcrypt(base64(hmac-sha384(pw, key=pepper)))` o migrar a **Argon2id**.

**Consecuencias.** Sin truncado silencioso ni colisiones por sufijo descartado. Cubierto por
`tests/integration/test_auth_api.py` (rechazo 422 en 73 bytes y aceptación en el límite de 72) y
`tests/unit/test_security.py` (la salvaguarda del hasher no lanza ante entradas largas).

---

## ADR-23 — Reproducibilidad del toolchain de verificación: la CI instala desde el lock

**Contexto.** ADR-19 hace reproducible el **runtime** (el `Dockerfile` instala desde un
`requirements.txt` fijado y verificado por hash). Las **puertas de calidad** (black, isort,
flake8, ruff, mypy y pytest) deben quedar igual de fijadas: si se instalaran con rangos `>=`, dos
máquinas (o dos momentos) podrían resolver versiones distintas de las herramientas y emitir
**veredictos distintos** sobre el mismo código sin cambiarlo.

**Decisión.** La CI instala el **entorno exacto del lock** con `uv sync --frozen --extra dev` y
ejecuta cada puerta con `uv run --frozen ...`. Así las herramientas de verificación quedan tan
fijadas como el runtime, y el lock es la fuente de verdad **también** del toolchain de calidad,
no solo de las dependencias de ejecución. Es el mismo criterio sin-divergencia de ADR-19/ADR-6,
aplicado a la capa que emite el veredicto.

**Por qué no fijar las dev-deps en `pyproject.toml`.** Fijar versiones exactas en el `pyproject`
duplicaría lo que `uv.lock` ya resuelve y entraría en conflicto con el modelo de uv (rangos de
entrada → lock de salida). La forma correcta es **instalar desde el lock**, no reescribir la
entrada de resolución.

**Consecuencias.** Las puertas son deterministas: el mismo commit produce el mismo veredicto en
cualquier máquina y momento, y una subida de versión de una herramienta entra de forma
**controlada** (regenerando el lock). `uv sync` también instala el proyecto, así que
`alembic`/`uvicorn`/`pytest` quedan disponibles vía `uv run`. Para el desarrollo local,
`uv sync --frozen --extra dev` es la vía canónica; `pip install -e ".[dev]"` se conserva como
atajo de conveniencia, con la salvedad de que **no fija** el toolchain de verificación.

---

## ADR-24 — Versionado de la API y política de deprecación

**Contexto.** Toda la superficie pública cuelga del prefijo `/api/v1`
([app/web/main.py](app/web/main.py)). El enunciado no pide versionado, pero exponer una API sin
una política explícita de evolución traslada al cliente el riesgo de un cambio incompatible
silencioso. Conviene dejar documentado **cómo** se introduce una versión nueva y **cómo** se
retira la antigua antes de que exista un `v2`.

**Decisión.** Versionado **por prefijo de ruta** (`/api/v1`, `/api/v2`…), no por cabecera ni por
tipo de medio. Es la opción más explícita y almacenable en caché, trivial de enrutar en un proxy
y la que mejor se ve en la Swagger UI; las alternativas (negociación por `Accept` o cabecera
`X-API-Version`) son más flexibles pero más fáciles de usar mal y de inspeccionar desde un
navegador o un `curl`. Reglas:

- **Qué obliga a subir de versión.** Solo un cambio **incompatible** (eliminar/renombrar un campo
  o endpoint, endurecer una validación, cambiar el tipo de una respuesta). Las adiciones
  compatibles (un campo nuevo opcional, un endpoint nuevo, un valor de enum nuevo) se hacen
  **dentro de `v1`**: los clientes deben tolerar campos desconocidos (evolución solo aditiva).
- **Cómo se retira una versión.** Cuando nazca `/api/v2`, `v1` no se apaga de golpe: entra en un
  periodo de deprecación anunciado en las **cabeceras HTTP estándar** de sus respuestas:
  - `Deprecation` (**RFC 9745**, 2025): marca el recurso como deprecado e indica, opcionalmente,
    la fecha en que pasó a estarlo.
  - `Sunset` (**RFC 8594**): fija la fecha a partir de la cual `v1` dejará de responder.
  - `Link` con `rel="successor-version"` y `rel="deprecation"` apuntando al endpoint `v2` y a la
    guía de migración.
- **Implementación cuando aplique.** Una dependencia/middleware de FastAPI montada solo sobre el
  router `v1` inyectaría esas cabeceras; mientras no haya nada deprecado **no se añade código
  muerto** (coherente con el resto del proyecto: no se construye un mecanismo para un caso que aún
  no existe). El `version` de la app FastAPI (`0.1.0`, [app/web/main.py](app/web/main.py))
  versiona el *artefacto*, distinto del contrato `v1`.

**Por qué no implementarlo ya.** No existe un `v2` ni nada deprecado; emitir hoy cabeceras
`Deprecation`/`Sunset` sería ruido sin destinatario. Lo correcto es **fijar la política** (este
ADR) para aplicarla sin discusión cuando llegue el primer cambio incompatible.

**Consecuencias.** El contrato evoluciona de forma predecible: lo compatible vive en `v1` y no
rompe a nadie; lo incompatible nace en `v2` y `v1` se retira con un preaviso legible por máquinas
(RFC 9745 + RFC 8594) en lugar de un apagón sorpresa. Referencias:
[RFC 9745](https://www.rfc-editor.org/info/rfc9745/),
[RFC 8594](https://www.rfc-editor.org/info/rfc8594/).

---

## ADR-25 — Fortaleza del `JWT_SECRET_KEY`: piso de entropía en producción

**Contexto.** ADR-5 exige que el secreto de firma sea **obligatorio** y de **≥ 32 caracteres**
(`min_length`), y fuera de desarrollo rechaza el secreto de ejemplo conocido. RFC 7518 §3.2 pide
para HS256 una clave de al menos **256 bits de longitud** (lo cubre `min_length=32` caracteres ≥
32 bytes). Pero **la longitud no garantiza fortaleza**: la guía OWASP advierte que "una clave
larga con baja entropía es menos segura que una corta con alta entropía", y un secreto HMAC débil
se rompe por fuerza bruta en segundos. Un secreto débil pero distinto del de ejemplo (p. ej.
`"changeme"` rellenado hasta 32 caracteres) pasaría a producción solo con la denylist.

**Decisión.** **Solo fuera de desarrollo**, se añade un **piso de entropía**: la app **no
arranca** si la **entropía de Shannon estimada** del `JWT_SECRET_KEY` es inferior a **112 bits**.
112 bits es el nivel de seguridad simétrica "aceptable" de **NIST SP 800-57** (margen hasta
~2030); un secreto generado al azar (`secrets.token_urlsafe(48)` ≈ 334 bits, o 32 hex ≈ 128 bits)
lo supera con holgura, mientras que uno débil o de relleno queda por debajo. El cálculo es una
función pura (`_estimate_secret_entropy_bits`), sin I/O.

**Por qué entropía y no solo una lista más larga.** Una lista de denegación fija solo cubre los
valores que alguien pensó en añadir; la entropía rechaza **toda** la clase de secretos débiles
sin enumerarlos. Se conservan ambos controles: la lista da un mensaje específico para el secreto
de ejemplo versionado, y el piso de entropía atrapa el resto.

**Contrapartida y alcance.** La estimación asume símbolos independientes (no modela patrones
secuenciales como `abcabc`), pero es determinista, barata y separa de forma fiable lo aleatorio
de lo débil, que es lo que importa aquí. **No** se aplica en desarrollo para no añadir fricción
al arranque local. No se sube el `min_length` global a 64 para no romper despliegues con secretos
válidos de 32-63 bytes ya en uso: el piso de entropía ataca la causa real (calidad), no la
longitud. Cubierto por `tests/unit/test_config.py`.

---

## ADR-26 — Concurrencia optimista en tareas: `version_id_col` + ETag/`If-Match`

**Contexto.** Las escrituras de una tarea (`PUT`, `PATCH /status`, `PATCH /assignee`, `DELETE`)
leen-y-escriben dentro de la transacción de la request: el servicio lee la tarea (autorización +
estado) y luego la actualiza. Dos editores que leen la misma tarea y guardan en paralelo
producirían una **actualización perdida** (el segundo pisa al primero sin avisar). Es la clase de
problema que el enunciado no menciona pero que un servicio multiusuario real debe resolver.

**Decisión.** Control de **concurrencia optimista** en dos niveles, sin bloqueos pesimistas:

1. **Modelo (BD).** `TaskModel` tiene una columna `version` declarada como
   [`version_id_col`](https://docs.sqlalchemy.org/en/20/orm/versioning.html) de SQLAlchemy: cada
   `UPDATE`/`DELETE` por ORM añade `WHERE version = :actual` y la incrementa; si ninguna fila
   coincide (otra transacción ya la cambió) lanza `StaleDataError`, que el repositorio traduce a
   **412**. Cierra la ventana TOCTOU residual entre la lectura del servicio y el `flush`.
2. **API (HTTP).** Cada tarea expone su versión como **ETag fuerte** (`"<version>"`) en las
   respuestas individuales. Las mutaciones exigen la precondición **`If-Match`**:
   - ausente → **428 Precondition Required** (RFC 6585 §3, definido **literalmente** para el
     problema de actualización perdida);
   - presente pero distinta de la versión actual → **412 Precondition Failed** (RFC 9110);
   - `If-Match: *` → comodín "cualquier versión vigente" (la existencia ya la garantiza la
     autorización).

   La comparación de versión vive en el caso de uso (`TaskService._ensure_version`); el parseo de
   la cabecera, en `app/web/etag.py`. La capa de dominio permanece agnóstica de HTTP: emite
   `PreconditionRequiredError`/`PreconditionFailedError` con su `status_code`.

**Por qué obligatorio (428) y no opcional.** Un `If-Match` opcional no cierra la brecha: un
cliente que lo omite sigue compitiendo. Exigirlo en las mutaciones de una tarea **existente** es
la única forma de garantizar que ninguna escritura se base en un estado que ya cambió. La
creación (`POST`) queda exenta (no hay estado previo) y devuelve el ETag inicial.

**Por qué optimista y no un bloqueo.** El conflicto es raro (dos ediciones simultáneas de la
misma tarea); pagar un `SELECT ... FOR UPDATE` en cada escritura penalizaría el caso común. El
contador de versión solo cuesta una columna y no retiene bloqueos entre requests.

**Consecuencias.** El `UPDATE` masivo de `clear_assignee_in_list` (al expulsar a un colaborador)
**no** pasa por `version_id_col` (solo el `flush` de filas individuales lo hace), así que
incrementa `version` de forma **explícita** para conservar la invariante "toda modificación sube
la versión". Cubierto por `tests/unit/test_task_service.py`, `tests/unit/test_etag.py` y
`tests/integration/test_tasks_api.py`. La migración `0003_task_version` añade la columna con
`server_default="1"` (siembra las filas existentes).

**Referencias.**
[SQLAlchemy — Configuring a Version Counter](https://docs.sqlalchemy.org/en/20/orm/versioning.html),
[RFC 9110 §13.1.1 (If-Match) / §15.5.13 (412)](https://www.rfc-editor.org/rfc/rfc9110),
[RFC 6585 §3 (428 Precondition Required)](https://www.rfc-editor.org/rfc/rfc6585#section-3).

---

## ADR-27 — Puertas de calidad sobre todo el repo + formateo automático de migraciones

**Contexto.** Los comandos de calidad documentados en el README (`black .`, `isort .`,
`ruff check .`) cubren **todo** el repo. Para que el veredicto sea consistente, la CI debe cubrir
exactamente lo mismo, incluidas las migraciones autogeneradas por Alembic, que de otro modo
podrían no respetar el orden de imports y el formato del resto del proyecto.

**Decisión.** Cubrir y **hacer cumplir** de forma consistente, sin excluir las migraciones:

1. **Migraciones conformes.** Los imports de las migraciones y de `env.py` siguen el mismo orden
   canónico que el resto (isort, black y ruff coinciden en la salida: no hay tensión entre
   ellos).
2. **Plantilla canónica.** `migrations/script.py.mako` emite los imports en orden canónico
   (dentro de la sección de terceros, los `import x` antes que los `from x import y`), para que el
   formato no se rompa con cada migración nueva.
3. **Formateo automático al generar.** `[post_write_hooks]` en `alembic.ini` ejecuta **isort →
   black → `ruff --fix`** sobre cada archivo recién generado (`alembic revision
   [--autogenerate]`), con el mismo toolchain que las puertas. Cada migración nace conforme
   (orden de imports, longitud de línea, sin imports sin usar).
4. **Puertas sobre todo el repo.** La CI ejecuta black/isort/flake8/ruff sobre `.` (no solo
   `app tests`), incluyendo `migrations/` y la raíz, alineado con los comandos del README. `mypy`
   sigue acotado a `app`: las migraciones autogeneradas no son superficie tipada. Todas las
   herramientas excluyen `.venv` por defecto.

**Por qué cubrir y no excluir `migrations/`.** Excluir las migraciones de los linters las dejaría
sin verificar imports sin usar (F401) ni formato en código que **sí se ejecuta** (crea el
esquema). El flujo recomendado por la documentación de ruff es ordenar imports y luego formatear,
no añadir exclusiones. El riesgo conocido de orden de imports **inestable entre máquinas** para
Alembic/SQLAlchemy ([ruff#21638](https://github.com/astral-sh/ruff/issues/21638)) queda mitigado
por el toolchain fijado de ADR-23: el veredicto es determinista.

**Consecuencias.** Los comandos del README son **los mismos** que impone la CI; una migración
nueva nace formateada y no rompe la puerta. Es el criterio sin-divergencia de ADR-6/ADR-19/ADR-23
extendido al **alcance** de las puertas. Una migración **vacía** (sin operaciones) puede marcar
`F401` por `op`/`sa` sin usar; es señal correcta (un archivo de migración sin cuerpo es un error)
y desaparece en cuanto la migración hace algo.

**Referencias.**
[Alembic — post-write hooks](https://alembic.sqlalchemy.org/en/latest/autogenerate.html#applying-post-processing-and-python-code-formatters-to-generated-revisions),
[ruff#21638 (orden de imports Alembic/SQLAlchemy)](https://github.com/astral-sh/ruff/issues/21638).

---

## ADR-28 — Persistencia políglota (NoSQL): estrategia documentada, introducción diferida

**Contexto.** La **vacante** (no el enunciado de la prueba) pide manejo de bases de datos **SQL y
NoSQL** (MySQL, PostgreSQL, MongoDB, etc.). El núcleo de este servicio es deliberadamente
**transaccional** —unidad de trabajo por request, outbox transaccional (ADR-15) y concurrencia
optimista (ADR-26)— y vive en **PostgreSQL/SQLAlchemy**. El NoSQL **clave-valor ya está en el
código**: Redis es el backend opcional del límite de login (ADR-17). Lo que falta para cubrir la
vacante por completo es un **almacén de documentos**. La decisión es **no** introducirlo ahora
(sería ampliar el alcance y arriesgar invariantes del núcleo) y documentar **cuándo** y **cómo**
entraría, sin reescribir casos de uso.

**Decisión (camino, no implementado).** Cuando el dominio lo justifique, añadir NoSQL **detrás de
los puertos existentes** (las cuatro capas permiten una incorporación aditiva):

1. **MongoDB como almacén de documentos para un registro de actividad/auditoría.** Eventos
   solo-anexar, esquema flexible y lectura por línea de tiempo: el caso de uso canónico de un
   documento. Se modela con un puerto nuevo `IActivityLogRepository` en `domain`, un adaptador en
   `infrastructure` y un doble en memoria en tests (igual que el resto). Va **deliberadamente
   FUERA de la unidad de trabajo transaccional** (mejor esfuerzo, eventualmente consistente):
   auditar nunca debe poder abortar una escritura de negocio ni acoplar dos motores en una
   transacción distribuida. Si se exigiera durabilidad fuerte, se enruta por el **mismo patrón
   outbox** de ADR-15 (persistir el evento en PostgreSQL dentro de la transacción y proyectarlo a
   MongoDB con un worker).
2. **Driver: PyMongo Async (`AsyncMongoClient`, `pymongo>=4.13`), NO Motor.** Motor está
   **deprecado desde 2025-05-14 (fin de vida 2026-05-14)** en favor de la API asíncrona
   **nativa** de PyMongo, que además rinde mejor (asyncio integrado en vez de un grupo de hilos).
   Elegir Motor hoy sería deuda técnica desde el primer commit.
3. **Redis como caché de lectura opcional** de lecturas calientes (p. ej. `completion_stats`, hoy
   una agregación por request — ADR-7), con invalidación en la escritura. Reutiliza la dependencia
   y la experiencia ya presentes (ADR-17).
4. **Pruebas:** doble en memoria para unit (como el resto de puertos) y **MongoDB real como
   servicio en CI** para integración (mismo patrón que el job de PostgreSQL del `ci.yml`), más
   fiable que `mongomock`.

**Por qué diferirlo.** El enunciado pide explícitamente priorizar lo principal y documentar lo
pendiente. El dominio actual (listas/tareas con invariantes transaccionales) **no tiene una
necesidad real** de documento ni de caché; adelantarlos sería sobre-ingeniería que contradice la
disciplina de alcance de esta entrega. Como los puertos hacen la incorporación **aditiva** (un
puerto + un adaptador, sin tocar los casos de uso), el costo de diferir es bajo y el de adelantar
(complejidad y un motor más que operar) es real.

**Consecuencias.** La capacidad NoSQL queda **demostrada en código** (Redis clave-valor, ADR-17)
y **diseñada** para el almacén de documentos sin acoplar el núcleo. El día que entre MongoDB no se
reescribe lógica de negocio.

**Referencias.**
[MongoDB — Migrate to PyMongo Async](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/),
[Motor — aviso de deprecación](https://www.mongodb.com/docs/drivers/motor/).

---

## ADR-29 — Despliegue en producción: Kubernetes/GKE + IaC (Terraform) + despliegue sin claves, diferido

**Contexto.** La **vacante** pide contenedores/**Kubernetes**, **CI/CD** y **nube**
(AWS/GCP/Azure), con **IaC/Terraform** como deseable. El repo ya entrega lo que hace a la app
desplegable: imagen multistage endurecida (sin privilegios, sistema de archivos de solo lectura,
base por digest, dependencias con hash — ADR-16), **healthchecks** liveness/readiness pensados
para orquestadores (ADR-10), configuración por entorno (12-factor, ADR-5), **migraciones
idempotentes** en el arranque (ADR-6) y **CI** completa. Faltan los **manifiestos** y la **IaC**,
que son artefactos de **plataforma** (dependen de un entorno destino real), no de la prueba.

**Decisión (camino, no implementado).** Desplegar en **GKE** con:

1. **Manifiestos Kubernetes vía Kustomize** (base + capa de producción): `Deployment` (sondas a
   `/health` y `/health/ready` **ya existentes**; `resources` con requests/limits;
   `securityContext` `runAsNonRoot` coherente con el Dockerfile), `Service`, `HPA` (CPU/RPS),
   **`Job` de migraciones** (`alembic upgrade head`) como paso previo al despliegue (en lugar del
   arranque de cada réplica cuando haya muchas), `ConfigMap` para configuración no sensible y
   `Secret` inyectado desde **Secret Manager** (CSI driver) o *External Secrets*, más
   `NetworkPolicy`. El **worker del outbox** y la **purga de tokens** como `Deployment`/`CronJob`
   dedicados con `RUN_OUTBOX_WORKER_IN_PROCESS=false` (ADR-15/21). El límite compartido en Redis
   (ADR-17) como dependencia gestionada (Memorystore).
2. **IaC con Terraform:** GKE (Autopilot o grupos de nodos), **Artifact Registry**, *Workload
   Identity* (vínculo entre la cuenta de servicio de Kubernetes y la de GCP) y la **Workload
   Identity Federation** para la CI.
3. **Despliegue sin claves:** un workflow de GitHub Actions que autentica a GCP por **Workload
   Identity Federation** (`google-github-actions/auth` + `get-gke-credentials`), **sin claves de
   cuenta de servicio de larga vida** —la práctica recomendada por Google— en coherencia con la
   postura de seguridad del repo (secreto obligatorio ADR-5/25, dependencias con hash ADR-19,
   Trivy ADR-16). Flujo: construir → publicar en Artifact Registry → `Job` de migración →
   despliegue con verificación de readiness.

**Por qué diferirlo.** Manifiestos e IaC dependen del **entorno destino real** (proyecto GCP, VPC,
dominios, backend de secretos) que no existe en un desafío. Materializarlos con valores ficticios
añade superficie que **nadie puede validar** y refuerza el riesgo de desproporción de alcance. La
decisión correcta es dejar la app **lista para desplegar** (ya lo está) y **documentar el plan**,
no fabricar infraestructura no verificable.

**Consecuencias.** Queda un plan de despliegue concreto y defendible; cuando exista el proyecto
destino, la app **no requiere cambios de código** para correr en GKE (configuración por entorno +
sondas + migraciones ya resueltas).

**Referencias.**
[Google Cloud — Keyless authentication from GitHub Actions (WIF)](https://cloud.google.com/blog/products/identity-security/enabling-keyless-authentication-from-github-actions),
[google-github-actions/auth](https://github.com/google-github-actions/auth).

---

## Pendientes documentados (camino a producción)

- **Concurrencia optimista en listas** con el mismo mecanismo de ADR-26 (`version_id_col` +
  ETag/`If-Match`): hoy solo se aplica a **tareas**; `PUT /lists/{id}` usa "la última escritura
  gana". La extensión es mecánica y se deja documentada para no ampliar el alcance sin necesidad.
- **RBAC global** (roles a nivel de organización) sobre el actual control por recurso (ADR-14).
- **Lista de denegación de access por `jti` en Redis** para revocación inmediata con varias
  réplicas (ADR-13).
- **Cola dedicada para el outbox** (Celery/arq) como evolución **opcional** (ADR-15). El worker ya
  puede ejecutarse como proceso dedicado (`RUN_OUTBOX_WORKER_IN_PROCESS=false` +
  `python -m app.infrastructure.outbox_worker`) y la toma con `FOR UPDATE SKIP LOCKED` lo hace
  seguro con varias réplicas; mover la entrega a una cola es una decisión operativa, no de
  correctitud.
- **Base distroless/Chainguard 3.12** cuando esté disponible (ADR-16).
- **Persistencia políglota (NoSQL):** almacén de documentos MongoDB (PyMongo Async) para un
  registro de actividad y caché de lectura en Redis, ambos detrás de puertos y fuera del núcleo
  transaccional. Estrategia y motivo del aplazamiento en **ADR-28**.
- **Despliegue en GKE + IaC + entrega sin claves:** manifiestos Kubernetes (Kustomize), Terraform
  (GKE/Artifact Registry/WIF) y despliegue por Workload Identity Federation sin claves. Plan
  completo en **ADR-29**.

> El límite de login compartido en **Redis** (ADR-17) y la derivación de la IP del cliente desde
> `X-Forwarded-For` tras proxies de confianza (ADR-17) **ya están implementados**.
