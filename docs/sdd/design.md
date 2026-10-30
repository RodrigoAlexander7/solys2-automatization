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

## Operación

Dos servicios Compose con volumen local común; solo web publica puerto. Simulador
en Docker por defecto. Para Windows real: adquisición nativa y web nativa o
contenedor con bind mount local compartido. No prometer COM automático en Linux
containers. .env central, variables de entorno prevalecen, CLI prevalece sobre ambas.
El Makefile usa Python para tareas portables y Bun para frontend. Docker es opcional.
