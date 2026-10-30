# Ejecutar adquisición y dashboard

## 1. Qué se ejecuta

Dos procesos Python: `log` posee Modbus y escribe SQLite; `dashboard` ejecuta
FastAPI, abre conexiones de lectura y sirve el build SvelteKit. Bun se usa para
instalar, probar y construir frontend. En producción no hay servidor Bun/Node.
La UI es de lectura; no cambia medidas ni escribe anotaciones.

Requisitos nativos: Python **3.13**, Bun **1.4**, navegador moderno y, opcionalmente,
GNU Make. Versiones resueltas: `requirements.lock` y `frontend/bun.lock`. Los
archivos con rangos describen dependencias; los constraints y Bun lock fijan las
versiones probadas. Validar cualquier actualización con los tests.

## 2. Arranque nativo

```sh
make install
make check
make test
make run
```

`install` crea `.env` desde `.env.example` si no existe, instala dependencias y
conserva cualquier configuración existente. `run` construye la UI y lanza ambos
procesos. Abrir **http://127.0.0.1:8050**. La plantilla usa simulación por defecto.
El fallo de la web no detiene la adquisición. Ctrl+C en el lanzador pide cerrar
ambos; si alguno no responde en 10 s se termina forzosamente. WAL conserva los
commits y el siguiente escritor marca como `interrupted` la corrida no finalizada.

Para operar y reiniciar cada proceso por separado, usar dos terminales:

```sh
make build
make acquire
```

En la segunda: `make web`. `make frontend` abre el servidor de desarrollo Vite
con proxy a la API del puerto 8050; la instalación normal usa el build estático.
Si cambia el puerto API, ajustar también el proxy de desarrollo en `vite.config.ts`.

### Windows sin Make

Desde PowerShell en la raíz:

```powershell
python scripts/manage.py env
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
bun install --cwd frontend --frozen-lockfile
bun run --cwd frontend build
.venv\Scripts\python scripts/manage.py run
```

Con GNU Make, usar `make install PYTHON=python`. Para hardware real, editar `.env`:
`SMP_SIMULATE=0` y `SMP_SERIAL_PORT=COM10` (o puerto detectado). Cerrar SmartExplorer.
`--simulate` / `--no-simulate` prevalecen sobre el archivo. Antes de una corrida
científica, confirmar con `info` el perfil SGR4 del firmware real.

## 3. Docker

Docker es opcional. Windows con sensores reales puede usar adquisición nativa
para evitar trasladar COM a una VM/contenedor. No se presupone que un COM de
Windows aparezca automáticamente dentro de un contenedor Linux.

| Objetivo | Comando | Qué inicia |
|---|---|---|
| Todo simulado | `make docker-up` | Adquisición simulada y web, volumen local compartido |
| Web con adquisición nativa | `make docker-web` | Solo web; lee el SQLite del mismo disco local |
| Construir | `make docker-build` | Build multietapa Bun → Python, sin Bun en runtime |
| Tests en imagen | `make docker-test` | Checks/build/Bun tests al construir y unittest en contenedor |
| Ver trazas | `make docker-logs` | Logs de ambos procesos |
| Detener | `make docker-down` | Retira contenedores/red; conserva datos de la carpeta montada |

Los comandos equivalentes son `docker compose up -d --build`, `docker compose
up -d --build web` y `docker compose down`. Para modo mixto, fijar `SMP_DATA_DIR`
en `.env` a la carpeta `data` **absoluta**, por ejemplo `C:/lab/solys/data`, y
dejar la adquisición nativa usando `SMP_DB_PATH=data/acquisition.sqlite3`.
Solo un escritor puede usar esa BD. No iniciar a la vez adquisición simulada
Docker y adquisición nativa sobre el mismo archivo.

Antes de usar Compose directamente, crear la carpeta de datos con el usuario
nativo (`python scripts/manage.py env` prepara `./data`). Si `SMP_DATA_DIR` apunta
a otra carpeta, crearla previamente. Los contenedores usan `SMP_UID`/`SMP_GID`
(1000/1000 por defecto); en Linux deben coincidir con `id -u`/`id -g` del
propietario de la carpeta. Así se evita crear archivos root que impidan luego
la ejecución nativa. La API abre SQLite en modo de lectura, pero WAL puede
necesitar acceso al directorio para sus archivos auxiliares compartidos.

SQLite WAL requiere coordinación de archivos locales. El modo mixto con bind
mount de Docker Desktop debe validarse en la versión concreta de Windows/Docker
(incluyendo locks y coherencia de WAL); la opción completamente nativa evita
esa frontera y es la recomendada si la prueba no la supera. No usar carpetas
SMB, OneDrive ni otros sincronizadores para la BD activa.

En Linux con sensor real y dispositivo disponible:

```sh
docker compose -f compose.yml -f compose.hardware.yml up -d --build
```

Configurar `SMP_DEVICE=/dev/ttyUSB0` o el dispositivo real. Esta variante no se
ha validado aquí con hardware. El Compose de ejemplo fuerza `--simulate` para
evitar que un arranque de demostración intente consultar instrumentos reales.
La web publica solo en loopback del host, puerto 8050 salvo override.

## 4. Configuración

| Variable | Función | Valor inicial |
|---|---|---|
| SMP_SIMULATE | Simulado o real | 1 en plantilla; sin archivo, la CLI conserva el valor real por defecto |
| SMP_DB_PATH | Archivo compartido SQLite | data/acquisition.sqlite3 |
| SMP_OUT_DIR / SMP_LABEL | Proyecciones CSV/PNG y etiqueta | data / laboratorio |
| SMP_SERIAL_PORT / SMP_BAUD / SMP_PARITY | Puerto y parámetros serie | auto / 19200 / E |
| SMP_TIMEOUT_S | Espera de una transacción | 0,3 s |
| SMP_ADDRESSES / SMP_PYRGEOMETERS | Equipos y subconjunto SGR4 | 2 3 4 / 2 |
| SMP_INTERVAL_S | Muestreo | 1 s |
| SMP_API_HOST / SMP_API_PORT | Escucha web | 127.0.0.1 / 8050 |
| SMP_POLL_S | Espera entre consultas SSE | 1 s |
| SMP_STALE_S | Antigüedad para dato obsoleto | 5 s |
| SMP_FRONTEND_DIR | Build servido | frontend/build |
| SMP_LOG_LEVEL | Trazas en stderr | INFO |
| SMP_DATA_DIR / SMP_DEVICE | Mount/dispositivo en Compose | ./data / /dev/ttyUSB0 |

Precedencia: CLI > entorno del proceso > `.env` > predeterminados. Los campos
de conexión/intervalo tienen opciones CLI; host/puerto web se configuran por env.
La UI no recibe `.env`. Compose cambia ruta interna de BD y host a `0.0.0.0`,
manteniendo la publicación externa en `127.0.0.1`.

## 5. API, históricos y exportaciones

Contrato interactivo en `/docs`; OpenAPI en `/openapi.json`.

| GET | Respuesta |
|---|---|
| /api/health | API viva y `database_ready`; la web puede estar viva antes de la primera corrida |
| /api/runs | Últimas corridas, hasta 100 por defecto |
| /api/runs/{id} | Configuración, identificación y estado de corrida |
| /api/runs/{id}/samples | Filas ordenadas por ID; filtros `address`, `start`, `end`, `after_id`, `limit` (máximo 5000) |
| /api/runs/{id}/status | Última lectura por equipo, antigüedad y comparación |
| /api/runs/{id}/minutes | Agregados reconstruidos: número, válidas, media, mínimo/máximo y desviación |
| /api/runs/{id}/stream | SSE `samples`, `heartbeat`, `failure`; acepta `after_id` y Last-Event-ID |

Fechas de filtro ISO 8601 con zona. `recent=true` pide las últimas `limit` filas,
devuelve cursor al final y `history_truncated` si omitió histórico; los GET normales
usan `has_more` para paginar. La UI carga hasta 3600 filas recientes, luego SSE,
deduplica por ID y conserva ese límite; muestra una ventana de hasta 15 min.
Elegir otra corrida permite inspeccionar su tramo final. Consultas de otro tramo
se hacen por API o CSV; no existe todavía selector temporal completo en la UI.

El cursor es de inserción, no de hora. Un dato tardío o una corrección de reloj
no lo deja fuera de la recuperación. No se mantienen transacciones abiertas
mientras el navegador espera eventos. Las consultas SSE son por cliente; si
se requieren muchos espectadores se podrá añadir fan-out compartido.

```sh
.venv/bin/python -m smp22x export RUN_ID --out data/exportacion
.venv/bin/python -m smp22x backup data/respaldo.sqlite3
.venv/bin/python -m smp22x plot data/exportacion
```

En Windows reemplazar el ejecutable por `.venv\Scripts\python`. La carpeta de
exportación y el archivo backup deben ser nuevos. Exportar fija un máximo de ID
al inicio; lo posterior no se incluye. `run.json` lleva ese `export_max_id`.
Backup usa la API SQLite; no copiar solo el `.db` mientras corre WAL.

## 6. Tests y diagnóstico

```sh
make check
make test
bun x --cwd frontend playwright install chromium
make build
bun run --cwd frontend test:e2e
```

E2E crea BD y procesos desechables; no usa el hardware ni la BD de producción.
Si ya dispone de Chromium compatible puede fijar `PLAYWRIGHT_CHROMIUM_EXECUTABLE`.
Capturas de pruebas en `test-results/` (ignoradas por Git).

Los mensajes humanos van a stdout; las trazas JSON a stderr. Buscar por `trace_id`
une petición y repositorio. `path` muestra, por ejemplo,
`HTTP GET /api/runs/.../samples -> SQLiteReader.samples`; incluye duración y error.
Los commits incluyen run_id y ciclo. Los errores inesperados conservan traceback
en el log, nunca en la respuesta del navegador. Un fallo de persistencia detiene
la corrida: no se comunica éxito ni se fabrican muestras cero.

Los errores esperados de repositorio son `Ok[T] | Err`; uniones de códigos en
`result.py`. La API los traduce a 404, 422 o 503 con código y trace_id. Un fallo
inesperado responde 500. Errores de sensor se conservan en cada muestra. La
[skill local](../skills/radiometry-results/SKILL.md) explica cómo mantener ese patrón.
Fue escrita siguiendo la guía skill-creator; no está instalada globalmente.

Faltan validación de hardware, pruebas sostenidas en la mini-PC y criterios
científicos para alertas de desviación. El detalle de evidencias está en
[SDD/tasks](sdd/tasks.md); no se confunden estos pendientes con tests ya ejecutados.
