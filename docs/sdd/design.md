# Diseño SDD

## Procesos y propiedad de datos

Adquisición → conversión → escritor SQLite. Otro proceso FastAPI abre conexiones
de solo lectura y sirve SvelteKit estático. Bun instala, prueba y compila el frontend.
No hay servidor Node de producción. No se inicia Modbus desde el ciclo de vida web.

SQLite es fuente de verdad. Las salidas CSV actuales son proyecciones posteriores
al commit y se pueden regenerar desde la BD; nunca se presentan como transacción
atómica junto con SQLite. Los promedios por minuto son consultas reconstruibles.

WAL, synchronous FULL, transacciones cortas, timeout acotado. Lock de escritor
a nivel del SO liberado al morir el proceso. Versionado con user_version; rechazo
de esquema desconocido. ID entero creciente por muestra y unicidad por corrida,
ciclo y equipo. La hora y el cursor son conceptos distintos.

API GET para histórico y SSE para novedades, ambos con el mismo cursor. Queries
acotadas, transacción cerrada antes de esperar, heartbeat independiente de muestras.
La UI conserva una ventana acotada y deduplica por ID. Una respuesta antigua no
puede reemplazar la selección actual. Errores de red son visibles con trace_id.

## Result y trazas

Fronteras de repositorio: `Ok[T] | Err` discriminadas por `ok`. Error con código,
mensaje y retryable. Fallos esperados se devuelven; el orquestador decide detener
una corrida cuando falla la persistencia. Excepciones inesperadas no se convierten
en éxito: se registran con traceback y pasan al handler de aplicación.

Decorador común de trazas con ContextVar para anidar operaciones y propagar
trace_id. JSON incluye operation, path, duration_ms, status y contexto explícito
(run_id, ciclo, dirección, request_id). No se registra .env ni cuerpos completos.
La API devuelve errores con código y trace_id, sin traceback al navegador.

No se cambia cada función pura a Result: conversiones totales y dataclasses
mantienen valores directos. Adaptadores Modbus existentes pueden lanzar BusError
y PortError; la lectura los clasifica como fallos explícitos de muestra.

## Transformaciones

S16/U16 y factores del manual convierten palabras a unidades físicas. No se
aplican nuevas calibraciones, filtros, corrección solar o compensación térmica
externa a las medidas del sensor. Se conserva Ld↓ leído; neta + σT⁴ es una
derivada aparte. Comparaciones y agregados se calculan con válidas; no interpolar.

## Ampliación 2: observatorio

### Almacenes y dueños

| Almacén | Único escritor | Contenido |
|---|---|---|
| `acquisition.sqlite3` (esquema 2) | Adquisición | Corridas, muestras tipadas, minutos, eventos, manifiesto de archivo |
| `observatory.sqlite3` | Web (FastAPI, un proceso, lock interno) | Versiones de configuración y bitácora |
| `archive/AAAA/MM/AAAA-MM-DD.parquet` | Hilo de archivo de la adquisición o `smp22x archive` | Muestras de 1 s de días UTC cerrados |

Cada proceso solo lee los almacenes de los demás. La configuración interpreta
datos; no cambia lo que la adquisición lee del bus.

### Esquema 2 de la base de adquisición

- `runs(pk INTEGER PK, id TEXT UNIQUE, …, conversion_version)`: la clave entera
  evita repetir 32 caracteres por muestra; la API sigue usando `id`.
- `samples(id, run, cycle, address, t_ms, sensor_type, ok, valid, error,
  error_code, mode, status, scale_factor, irradiance, raw_irradiance, lw_down,
  raw_lw_down, stdev, body_temp_c, supply_v, rh_pct, pressure_hpa, rtt_ms,
  reg_2 … reg_11, reg_16, reg_17)`. Hora en milisegundos UTC enteros. Índices:
  único `(run, cycle, address)`, cursor `(run, id)` y tiempo `(t_ms)`.
- `minutes(run, address, minute_ms, sensor_type, n_samples, n_valid, mean, min,
  max, std, net_mean, body_temp_mean, complete)`, clave `(run, address, minute_ms)`.
- `events(id, run, t_ms, address, kind, severity, detail JSON)`.
- `archive_days(day, path, rows, sha256, archived_ms, pruned_ms)`.
- `PRAGMA user_version=2`. `smp22x migrate` exige el lock de escritor, copia la
  BD con la API de backup, convierte, recalcula minutos, compara campo por campo
  y solo entonces cambia la versión.

El escritor recibe en `commit_cycle` las muestras, los minutos cerrados y los
eventos pendientes: todo o nada. Los derivados (`measurement`, `lw_down_calc`,
roles, QC) se calculan al leer.

### Eventos, reloj y archivo en la adquisición

- Eventos de flags: uno por cambio de palabra de estado y dirección, como máximo
  cada 10 s; al vencer la ventana se emite el estado actual con el número de
  cambios suprimidos. Sensor sin respuesta a los 5 fallos seguidos.
- Reloj: hilo `ClockMonitor` con SNTP (UDP 123), cada 600 s por defecto. Deja
  resultados en una cola; el bucle los convierte en eventos `clock_check` o
  `clock_unavailable` y en `status.clock`. Umbral por defecto 1 s (BSRN
  Operations Manual V2.1 §2.3.1).
- Archivo: hilo que, cada hora, escribe con conexión de solo lectura los días
  UTC terminados hace más de 1 h y sin manifiesto, o con más filas que el
  manifiesto. El bucle registra el manifiesto y poda en lotes de 2000 filas por
  ciclo los días vencidos, tras re-verificar SHA-256 y conteo.

### Configuración e interpretación

Versión de configuración: `{site, solys2_in_use, instruments[{address, shaded,
label}], qc{comparison_pct, shading_kt_min, shading_ratio_max}}`, con
`valid_from`, autor y nota. Sin versiones se usa una por defecto: sin sitio,
SOLYS2 inactivo y nada sombreado.

Rol de cada muestra en su instante:

| Tipo | Bola de sombra | SOLYS2 | Rol |
|---|---|---|---|
| Pirgeómetro | cualquiera | cualquiera | `LWD` |
| Piranómetro | no | cualquiera | `GHI` |
| Piranómetro | sí | en uso | `DHI` |
| Piranómetro | sí | inactivo | `SHADE_OFF` (sombra no garantizada) |

### Geometría solar y QC

`solar.py` implementa el algoritmo NOAA/Meeus vectorizado con numpy, refracción
dependiente de presión (atmósfera estándar desde la altura) y temperatura
(10 °C por defecto), y distancia Tierra-Sol. `Sa = 1361 W/m² / R²` y
`μ0 = max(cos z, 0)`.

| Prueba | Rol | Aplica | Falla si |
|---|---|---|---|
| BSRN físicamente posible | GHI | sitio conocido | `< −4` o `> 1,5·Sa·μ0^1,2 + 100` |
| BSRN físicamente posible | DHI | sitio | `< −4` o `> 0,95·Sa·μ0^1,2 + 50` |
| BSRN físicamente posible | LWD | siempre | `< 40` o `> 700` |
| BSRN extremadamente raro | GHI | sitio | `< −2` o `> 1,2·Sa·μ0^1,2 + 50` |
| BSRN extremadamente raro | DHI | sitio | `< −2` o `> 0,75·Sa·μ0^1,2 + 30` |
| BSRN extremadamente raro | LWD | siempre | `< 60` o `> 500` |
| BSRN cociente difusa/global | GHI y DHI | z < 93°, GHI > 50 | `DHI/GHI ≥ 1,05` (z < 75°) o `≥ 1,10` |
| Sombreado (proyecto) | DHI | z < 75°, GHI > 50, kt > 0,6 | `DHI/GHI > 0,8` |
| Comparación (proyecto) | pares del mismo rol | ambos válidos, media > 100 | diferencia relativa > 5 % |

Con varias GHI válidas en un ciclo se usa su media. Resultado por muestra:
`ok`, `fail` o `na` y motivo; resumen `bad` (falla física), `suspect` (otra
falla), `ok` o `na`. En minutos se aplican las mismas pruebas a las medias, con
el sol en el centro del minuto. DNI estimada solo con z < 80°.

### API

Lectura: `/api/live`, `/api/series`, `/api/minutes`, `/api/instruments`,
`/api/timeline`, `/api/observatory/config`, `/api/logbook`, `/api/export`,
`/api/figure` y las rutas anteriores, ahora con roles y QC. Escritura (solo
base del observatorio): `POST /api/observatory/config`, `POST /api/logbook`,
`POST /api/logbook/{id}/void`. Middleware: `Host` en lista local; escrituras con
`Origin` igual al host y `application/json`. Resolución automática de series:
1 s hasta 2 h, minutos después. Límites: exportación de 1 s hasta 31 días y de
minutos hasta 366 días.

### Interfaz

Rutas estáticas prerenderizadas: `/` monitor, `/historico`, `/instrumentos`,
`/bitacora`, `/documentacion`. Gráficos con uPlot (canvas, sin internet) y
cursor sincronizado; carpet plot en canvas. La documentación se compila desde
`docs/fisicos/*.md` con `marked`; los manuales se sirven en `/manuales` si
existe `references/`.

## Ampliación 3: bajo consumo

### Ventana liviana (`smp22x/ventana.py`)

- Proceso aparte que lee `acquisition.sqlite3` en modo solo lectura con una
  conexión propia, sin trazas por consulta. Cada segundo hace tres consultas
  indexadas: muestras con `t_ms ≥ ahora − 60 s`, `max(t_ms)` y la última
  corrida. Si la última lectura es más vieja que 60 s, la tabla muestra las
  últimas conocidas como «anterior».
- Dos `tk.Canvas` (onda corta por piranómetro, onda larga del pirgeómetro) que se
  vuelven a dibujar desde cero cada segundo: unas decenas de ítems. Un hueco
  mayor a 3 s o una lectura inválida corta la línea; un punto aislado se dibuja
  como círculo.
- Si `root.state() == "iconic"`, el ciclo no consulta ni dibuja; el evento
  `<Map>` de la ventana raíz fuerza un refresco inmediato.
- Exportación en un hilo: escribe en `*.part` y renombra al terminar. 1 s con
  `Reader.range_samples` y `history` (pyarrow) solo si el intervalo toca días
  podados; 1 min desde la tabla `minutes`. Mismos límites que la API (31 y 366 días).
- La ventana no lee la base del observatorio: #3/#4 son «piranómetro», #2 «pirgeómetro».

### Consola (`smp22x/console.py`) y trazas

- Formato `texto` (por defecto): stdout con líneas de ciclo, resumen y eventos;
  stderr solo para errores inesperados (traza con traceback) y HTTP 5xx; JSON
  completo en `data/logs/<comando>.jsonl` (5 MB × 5) al nivel `SMP_LOG_LEVEL`
  (por defecto WARNING). `emit` no serializa si el nivel está deshabilitado.
- Formato `json`: comportamiento anterior (todas las trazas a stderr).
- `dashboard` sin access log de uvicorn en formato texto.

### Histórico reducido

`/api/series?max_points=N`: si una serie supera N puntos, tramos de duración
`B` (múltiplo del paso base) con dos filas por tramo: la primera y la segunda
en orden temporal entre el mínimo y el máximo, ubicadas al inicio y a la mitad
del tramo; un tramo sin válidas devuelve una fila nula. La respuesta incluye
`bucket_ms` y el frontend usa `B/2` como paso. QC del tramo: el peor.

## Operación

Dos servicios Compose con volumen local común; solo web publica puerto. Simulador
en Docker por defecto. Para Windows real: adquisición nativa y web nativa o
contenedor con bind mount local compartido. No prometer COM automático en Linux
containers. .env central, variables de entorno prevalecen, CLI prevalece sobre ambas.
El Makefile usa Python para tareas portables y Bun para frontend. Docker es opcional.
