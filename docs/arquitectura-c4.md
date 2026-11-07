# Arquitectura actual: adquisición y dashboard

Dos procesos Python independientes y un navegador. El primero consulta los
instrumentos; el segundo presenta lo que ya se confirmó en SQLite. Instrucciones
de arranque en [operación](operacion-dashboard.md), conversiones en la
[guía de registros](lectura-registros.md).

## Vocabulario para un desarrollador web

| Concepto | Significado aquí |
|---|---|
| Equipo Modbus | Instrumento con dirección 2, 3 o 4; responde cuando se le consulta |
| Registro | Campo dentro del equipo; dirección PDU base cero, no dirección del instrumento |
| RS-485 / Modbus RTU | Enlace y protocolo del bus; peticiones secuenciales, un único maestro |
| PMU485 / COM | Conversor USB/RS-485 y puerto virtual del sistema operativo |
| Corrida (`run_id`) | Una sesión de adquisición, con configuración y equipos identificados |
| Ciclo (`cycle`) | Grupo de consultas secuenciales; no equivale a simultaneidad |
| Cursor de muestra (`id`) | Orden de inserción confirmado; permite reconectar sin depender del reloj |
| WAL | Modo SQLite que admite lectores mientras escribe la adquisición |
| SSE | Conexión HTTP que lleva novedades desde FastAPI al navegador |
| Build estático | Archivos de SvelteKit que FastAPI sirve; no hay proceso Bun en producción |
| Rol | Qué mide una lectura según el montaje vigente: GHI, DHI, LWD o sombra no garantizada |
| Versión de configuración | Estado del observatorio con fecha de vigencia; nunca se reescribe |
| Evento | Cambio de estado observado (flags, puerto, reloj, archivo), no una medición |

## C4 nivel 1: contexto

```mermaid
flowchart LR
  user([Investigador]) -->|"inicia corrida y consulta panel"| system["Sistema local de radiometría"]
  sensors["SGR4 #2; SMP22x #3 y #4"] -->|"respuestas Modbus vía PMU485"| system
  system -->|"SQLite y exportaciones CSV/PNG"| disk[(Disco local)]
  explorer[SmartExplorer] -.->|"configuración, nunca a la vez que adquisición"| sensors
```

La dirección 2 es pirgeómetro, no piranómetro. Se conservan neta y Ld↓; no se
promedian junto con la irradiancia de onda corta de #3 y #4. La compatibilidad
del perfil SGR4 antiguo sigue pendiente de confirmación de laboratorio.

## C4 nivel 2: contenedores y procesos

```mermaid
flowchart LR
  subgraph pc["PC Windows o Linux"]
    acq["Proceso Python: log<br/>Modbus, conversión, validez, eventos"]
    db[("SQLite WAL esquema 2<br/>runs, samples, minutes, events")]
    arch[("Parquet diario<br/>archive/AAAA/MM")]
    obs[("SQLite observatorio<br/>configuración y bitácora")]
    api["Proceso Python: dashboard<br/>FastAPI, QC, SSE, exportación"]
    browser["Navegador<br/>SvelteKit compilado con Bun"]
    light["Proceso Python: ventana<br/>Tkinter, último minuto y CSV"]
    db -->|"lectura cada 1 s; nada si está minimizada"| light
    files[("CSV, JSON y PNG")]
    acq -->|"escritor único, commit de ciclo"| db
    acq -->|"hilo de archivo; poda verificada"| arch
    db -->|"conexiones de lectura"| api
    arch -->|"lectura de días podados"| api
    api -->|"escritor único"| obs
    api -->|"HTTP, assets y SSE"| browser
    browser -->|"GET; POST JSON con Origin local"| api
    acq -->|"proyección tras commit"| files
  end
  sensors["SGR4 + SMP22x<br/>(SOLYS2 con bola de sombra)"] -->|"PMU485, COM, Modbus"| acq
  ntp["Servidor NTP"] -.->|"SNTP, solo medición del desfase"| acq
```

FastAPI no abre COM ni inicia adquisición. Al reiniciarlo, `log` sigue escribiendo.
SQLite no es un servicio adicional: es un archivo y una biblioteca embebida. Cada
almacén tiene un único escritor: la adquisición para mediciones y archivo, la
web para configuración y bitácora. Docker dispone dos contenedores con los
mismos procesos; el uso nativo sigue disponible para la mini-PC Windows.

## C4 nivel 3: componentes

```mermaid
flowchart TB
  cli["cli.py<br/>comandos y configuración"] --> acq["acquisition.py<br/>ciclos, estado y proyecciones"]
  cli --> api["api.py<br/>GET, SSE, errores y assets"]
  acq --> sensor["sensor.py<br/>Sample, decodificación y validez"]
  sensor --> regs["registers.py<br/>mapa y escalas"]
  sensor --> bus{{ModbusBus}}
  serial["transport.py: SerialBus"] -.-> bus
  sim["simulator.py: SimulatedBus"] -.-> bus
  acq --> events["events.py<br/>flags, silencio, modo"]
  acq --> minutes["minutes.py<br/>promedios de 1 min"]
  acq --> clock["clock.py<br/>SNTP en hilo aparte"]
  acq --> archive["archive.py<br/>Parquet, verificación, poda"]
  acq --> writer["storage.py: Writer<br/>lock SO y transacciones"]
  writer --> db[(SQLite)]
  db --> reader["storage.py: Reader<br/>histórico, backup, migración"]
  reader --> history["history.py<br/>base activa + Parquet"]
  history --> api
  api --> analysis["analysis.py<br/>roles, QC, derivados"]
  analysis --> qc["qc.py + solar.py<br/>BSRN, proyecto, posición solar"]
  api --> obs["observatory.py<br/>configuración y bitácora"]
  api --> export["exporting.py + plotting.py<br/>CSV, ZIP y PNG a pedido"]
  api --> ui["frontend/<br/>SvelteKit, uPlot, carpet, docs"]
  common["result.py, tracing.py, config.py<br/>fallos tipados, contexto y entorno"] -.-> acq
  common -.-> api
```

| Componente | Responsabilidad |
|---|---|
| `sensor.py` | Interpretar enteros según el perfil; preservar palabras originales; no recalibrar |
| `acquisition.py` | Planificar y secuenciar; confirmar ciclo (muestras, minutos, eventos) antes de CSV; detener ante error de almacenamiento |
| `events.py` | Convertir cambios de estado en eventos con límite de frecuencia |
| `clock.py` | Medir el desfase del reloj por SNTP sin tocarlo |
| `storage.py` | Esquema 2 tipado; único escritor; lectores cortos; migración verificada; backup |
| `archive.py` / `history.py` | Parquet diario verificado y poda segura; consultas que unen ambas fuentes |
| `observatory.py` | Versiones de configuración y bitácora de solo agregar |
| `analysis.py`, `qc.py`, `solar.py` | Rol vigente en cada instante, pruebas BSRN y del proyecto, DNI estimada; nunca modifican valores |
| `api.py` | Validar filtros; proteger Host/Origin; mapear errores; no entregar tracebacks al navegador |
| `exporting.py` / `plotting.py` | CSV en streaming, ZIP con metadatos y figuras PNG a pedido |
| `frontend/src/lib/data.ts` | Alineación de series, deduplicación, huecos sin interpolar; ausentes no son cero |
| `TimeChart.svelte` / `Carpet.svelte` | uPlot sin CDN con cursor sincronizado, zoom y PNG; carpet plot en canvas |
| `ventana.py` | Interfaz liviana Tkinter: tres consultas por segundo, dibujo en Canvas, exportación CSV clásica |
| `decimate.py` | Reducir series para dibujar (mínimo y máximo por tramo, huecos explícitos) |
| `console.py` | Líneas de ciclo, resúmenes y eventos legibles en la terminal |
| `tracing.py` | JSON con trace_id, cadena de operaciones, duración y contexto permitido |

## Recorrido de un ciclo

```mermaid
sequenceDiagram
  participant A as Adquisición
  participant S as Sensor/Modbus
  participant W as Escritor SQLite
  participant F as FastAPI
  participant U as SvelteKit
  A->>S: FC04, equipo 2, PDU 2, cantidad 10
  S-->>A: palabras U16
  Note over A: signo, escalas, validez; conservar originales
  A->>S: equipos 3 y 4, PDU 2, cantidad 8
  S-->>A: palabras U16
  A->>W: muestras del ciclo + estado
  W-->>A: commit confirmado o Err
  Note over A: proyecciones CSV después del commit
  F->>W: consulta incremental de solo lectura
  W-->>F: muestras confirmadas con ID
  F-->>U: SSE con cursor
  Note over U: deduplicar, limitar ventana y actualizar calidad
```

No se requiere que web esté activa cuando se confirma el ciclo. Al reconectar,
la UI recupera IDs posteriores al cursor. Los tiempos de cada lectura son del PC
y difieren dentro del ciclo. Las derivadas y limitaciones científicas están en
[tratamiento de datos](tratamiento-datos.md).

## Comportamiento ante fallos

| Fallo | Comportamiento |
|---|---|
| Sensor sin respuesta | Muestra fallida con error explícito; backoff tras cinco fallos |
| USB desconectado | Reintentos; estadísticas separadas de fallos de sensor |
| Escritura SQLite fallida | Err tipado y corrida detenida con error; no comunicar datos como confirmados |
| Dos adquisiciones para la misma BD | La segunda falla con `writer_active` |
| Proceso muerto abruptamente | Locks del SO liberados; commits completos recuperables; siguiente escritor marca corrida interrumpida |
| API/navegador cerrado | Adquisición continúa; GET/SSE recuperan por cursor |
| Mapa/escala desconocidos | Advertencia o muestra inválida según caso; no inventar una conversión |
| Servidor NTP inaccesible | Evento `clock_unavailable` y aviso; la adquisición sigue |
| Archivo Parquet alterado o ausente | Evento `archive_failed`; el día no se poda |
| Filas tardías en un día archivado | El día se reescribe uniendo archivo y base antes de volver a podar |
| Base de esquema 1 | Adquisición y web se niegan e indican `smp22x migrate` |
| Pedido con `Host` u `Origin` ajeno | 403; ninguna escritura |

Los bugs previos de baudios, desconexión contada como fallo de sensor y mutación
de argumentos de scan conservan tests de regresión. Evidencias actuales en
[SDD/tasks](sdd/tasks.md).
