# Ejecutar adquisición y dashboard

## 1. Qué se ejecuta

Dos procesos Python: `log` posee Modbus, escribe la base de mediciones, comprueba
el reloj y archiva en Parquet; `dashboard` ejecuta FastAPI, lee las mediciones,
escribe solo la base del observatorio (configuración y bitácora) y sirve el
build SvelteKit. Bun se usa para instalar, probar y construir frontend. En
producción no hay servidor Bun/Node. La web nunca cambia medidas ni escribe en
los sensores.

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

En la segunda: `make web`. La instalación normal sirve el build estático en
http://127.0.0.1:8050. Para **desarrollar el frontend con recarga automática**,
abrir una tercera terminal con `make frontend` o `bun run --cwd frontend dev`
y visitar **http://127.0.0.1:5173** (o el puerto que muestre Vite si está ocupado).
Ese servidor usa un proxy a la API del puerto 8050. No requiere reconstruir el
frontend después de cada edición. No arrancar también `make run`, porque ya
inicia adquisición y API. El [README](../README.md#iniciar-el-frontend-en-desarrollo)
incluye los tres comandos para Linux/macOS y Windows.
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
| SMP_OBSERVATORY_DB | Configuración versionada y bitácora (escribe la web) | junto a SMP_DB_PATH |
| SMP_ARCHIVE_DIR | Archivo Parquet diario | junto a SMP_DB_PATH, `archive/` |
| SMP_RETENTION_DAYS | Días de 1 s en la base activa; 0 = no podar | 30 |
| SMP_NTP_SERVER | Servidor para comprobar el reloj; vacío = deshabilitado | pool.ntp.org |
| SMP_CLOCK_CHECK_S / SMP_CLOCK_MAX_OFFSET_S | Frecuencia y umbral de aviso del reloj | 600 s / 1 s |
| SMP_SIM_LAT / SMP_SIM_LON / SMP_SIM_ALT | Sitio del simulador | referencia NREL SPA (Golden, CO) |
| SMP_SIM_SHADED / SMP_SIM_TRACKER | Piranómetros simulados con bola de sombra y seguidor activo | vacío / 1 |
| SMP_ALLOWED_HOSTS | Nombres extra aceptados en `Host` (lista separada por comas) | vacío |
| SMP_CONTAINER_LOOPBACK | Permite escuchar en 0.0.0.0 dentro de un contenedor publicado en loopback | 0 (Compose: 1) |

Precedencia: CLI > entorno del proceso > `.env` > predeterminados. Los campos
de conexión/intervalo tienen opciones CLI; host/puerto web se configuran por env.
La UI no recibe `.env`. Compose cambia ruta interna de BD y host a `0.0.0.0`,
manteniendo la publicación externa en `127.0.0.1`.

**Sin autenticación:** la web atiende solo pedidos con `Host` local y rechaza
escrituras sin `Origin` propio o que no sean JSON. Si `SMP_API_HOST` no es
loopback, `dashboard` se niega a arrancar. Abrirla a la red requiere agregar
autenticación primero.

| SMP_LOG_FORMAT | `texto` (terminal legible, trazas a archivo) o `json` (formato anterior) | texto |
| SMP_LOG_LEVEL | Nivel del archivo de trazas: WARNING solo fallos, INFO cada operación | WARNING |
| SMP_LOG_DIR | Carpeta de `<comando>.jsonl` (5 MB × 5) | junto a SMP_DB_PATH, `logs/` |
| SMP_CONSOLE_SUMMARY_S | Adquisición: 0 = una línea por ciclo; N = promedios cada N s | 0 |

## 4a. Interfaz liviana y terminal

En la mini-PC conviene usar la ventana liviana en lugar del navegador:

```sh
make run-ligero              # adquisición + ventana; o por separado:
make acquire                 # terminal 1
make ventana                 # terminal 2 (Windows: .venv\Scripts\python -m smp22x ventana)
```

La ventana usa Tkinter, que trae el instalador oficial de Python para Windows
(opción «tcl/tk and IDLE», marcada por defecto). Se comprueba con
`python -m tkinter`. No necesita `dashboard` ni Chrome.

La terminal de la adquisición muestra una línea por ciclo y los eventos:

```
12:04:31  #2 Ld↓   320,4   #3   812,3   #4   798,1  W/m²   ciclo   62 ms
12:04:32  #2 Ld↓   320,6   #3   812,0   #4     ---  W/m²   ciclo  361 ms   #4 sin respuesta
12:05:00  AVISO  #3 flags: activados DESICCANT_WARNING
```

`--resumen 10` imprime promedios cada 10 s y `--quiet` deja solo avisos y
errores. Las trazas JSON van a `data/logs/log.jsonl` (y `dashboard.jsonl`,
`ventana.jsonl`); en la terminal solo aparece lo inesperado, con su código de
traza para buscarlo en ese archivo.

**Medir el consumo en la mini-PC** (requiere `requirements-dev.txt`):

```sh
.venv/bin/python scripts/medir_consumo.py --demo --visible --segundos 120
.venv/bin/python scripts/medir_consumo.py --proceso chrome --segundos 120
```

La segunda medición suma todos los procesos de Chrome con el dashboard abierto.

## 4b. Actualizar una instalación existente (esquema 1 → 2)

La versión 2 guarda columnas tipadas. Con una base anterior, la adquisición y la
web se niegan a abrirla e indican el comando. Con **ambos procesos detenidos**:

```sh
.venv/bin/python -m smp22x migrate          # o: make migrate
```

Crea primero `acquisition.v1-backup-AAAAMMDD_HHMMSS.sqlite3` sin modificar,
convierte, recalcula los minutos y compara cada muestra antes de reemplazar el
archivo. Si algo no coincide, la base original queda intacta.

## 4c. Archivo Parquet y retención

La adquisición archiva sola cada día UTC una hora después de que termina y
retira de la base activa los días más antiguos que la retención, solo después de
verificar el archivo. Con la adquisición detenida se puede forzar:

```sh
.venv/bin/python -m smp22x archive --retention-days 30   # o: make archive
```

El estado queda en `/api/archive` y en la línea de tiempo (`archive_written`,
`archive_pruned`, `archive_failed`). Copie `data/archive/` a otro disco: es el
histórico de 1 s.

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
| /api/live | Estado de la última corrida: lecturas con rol y QC, comparaciones, reloj, alertas |
| /api/series | Serie entre corridas por fecha; `resolution` auto (1 s hasta 2 h), `1s` (hasta 6 h) o `1min`; `max_points` reduce cada serie para dibujar (mínimo y máximo por tramo) |
| /api/minutes | Promedios de minuto con QC (carpet plot), hasta 92 días |
| /api/instruments | Identificación leída, estadísticas de comunicación, última lectura y configuración |
| /api/timeline | Eventos de adquisición, bitácora y versiones de configuración, más recientes primero |
| /api/observatory/config | Configuración vigente, historial y tipos de sensor conocidos |
| /api/logbook | Notas de la bitácora (incluye anuladas salvo `include_voided=false`) |
| /api/export | CSV (`format=csv`) o ZIP con metadatos (`format=zip`); `resolution` 1s (≤ 31 días) o 1min (≤ 366); `invalid` include/exclude |
| /api/export/estimate | Filas y tamaño aproximado antes de descargar |
| /api/figure | PNG de matplotlib a pedido; no se guarda |
| /api/clock, /api/archive | Últimas comprobaciones de reloj; manifiesto del archivo |

| POST (solo base del observatorio) | Efecto |
|---|---|
| /api/observatory/config | Nueva versión con autor, nota y vigencia; crea entrada de bitácora |
| /api/logbook | Nueva nota |
| /api/logbook/{id}/void | Anula una nota con motivo (no la borra) |

Fechas de filtro ISO 8601 con zona. `recent=true` pide las últimas `limit` filas,
devuelve cursor al final y `history_truncated` si omitió histórico; los GET normales
usan `has_more` para paginar. El monitor carga solo el último minuto y sigue en
vivo por SSE deduplicando por ID. El histórico consulta cualquier intervalo
uniendo base activa y Parquet y pide `max_points=600` para dibujar.

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
make test-e2e
make test-perf        # incluye 30 días sintéticos (minutos)
make test-hardware    # bus real, adquisición y SmartExplorer cerrados
```

La suite está organizada por niveles: conversión (incluye propiedades con
`hypothesis`), reglas de QC en sus bordes, geometría solar contra NREL SPA,
escenarios de falla simulados (domo sucio, desnivel, bola desalineada, SOLYS2
detenido, offset nocturno, desecante, VOID_DATA, sensor mudo), almacenamiento,
migración y archivo, configuración, seguridad, API, frontend, rendimiento y
hardware opcional. El nivel hardware guarda volcados de registros en
`tests/fixtures/hardware/`, que luego se reconvierten en cada corrida de tests.

E2E crea BD y procesos desechables; no usa el hardware ni la BD de producción.
Siembra 24 h simuladas antes de arrancar la adquisición en vivo. Para verla a
mano: `.venv/bin/python scripts/e2e_server.py` y abrir http://127.0.0.1:8061.
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

Faltan validación de hardware, pruebas sostenidas en la mini-PC y validar con
mediciones reales los umbrales del proyecto (sombreado y comparación). El detalle
de evidencias está en [SDD/tasks](sdd/tasks.md); no se confunden estos pendientes
con tests ya ejecutados.
