# Especificación SDD: adquisición y dashboard local

Estado inicial: especificado antes de implementación. La evidencia de ejecución
y el estado de aceptación se registran en [tasks.md](tasks.md).

## Requisitos verificables

| ID | Requisito | Aceptación |
|---|---|---|
| R01 | Adquisición Python independiente de la web | API detenida o reiniciada no impide nuevas muestras |
| R02 | SQLite WAL, un escritor, commit por ciclo | Lectura concurrente, rollback y rechazo de segundo escritor probados |
| R03 | Trazabilidad científica | Persistir enteros originales, factor, versión de conversión, tipo, hora UTC, estado y errores |
| R04 | API de lectura acotada | Corridas, muestras, estado y agregados; filtros, límite, cursor creciente y errores tipados |
| R05 | Entrega incremental recuperable | GET y SSE después del commit; reconectar por ID sin pérdidas ni duplicados |
| R06 | SvelteKit + TypeScript + Bun | Build estático servido por FastAPI; selección de corrida, tabla, gráficos, datos obsoletos y errores visibles |
| R07 | Integridad de mediciones | Sin corrección adicional de irradiancia; derivadas etiquetadas; ausentes ≠ cero; inválidos conservados |
| R08 | Diagnóstico de fallos | Result tipado en fronteras nuevas; trazas JSON con cadena, duración, trace_id y contexto; errores inesperados con traceback |
| R09 | Configuración y operación reproducibles | .env.example, Make nativo/Docker, Compose simulado y web con adquisición Windows nativa |
| R10 | Compatibilidad de archivos | CSV/PNG existentes utilizables; exportación desde SQLite independiente del frontend |

## Casos límite obligatorios

- C01: DB inexistente, sin corridas, corrida sin muestras y esquema desconocido.
- C02: lector concurrente; segundo escritor; bloqueo de SQLite, disco lleno/E/S,
  transacción revertida, proceso terminado abruptamente y recuperación del lock.
- C03: timeout Modbus, puerto perdido, backoff, bloque corto o fuera de U16,
  escala desconocida, inválido con respuesta y diagnóstico antiguo no fiable.
- C04: reconexión con cursor, muestras insertadas durante carga inicial,
  límite/paginación, corrida distinta y cursor de otra corrida.
- C05: UTC obligatorio, intervalo invertido, parámetros fuera de rango, filtros
  vacíos, registro tardío, reloj corregido y ausencia de datos sin inventar ceros.
- C06: comparación con irradiancia cercana a cero, sensores sin pareja,
  Ld↓ no disponible y neta negativa válida; no mezclar magnitudes.
- C07: API o navegador caído, lectura antigua, cola visual acotada, salto de
  corrida, respuestas fuera de orden y assets sin internet.
- C08: cierre Ctrl+C/SIGTERM, fallo al iniciar, archivo de salida existente,
  exportación y backup con adquisición activa, .env inválido y overrides CLI.

No se presupone confirmación del mapa SGR4 data model 102. La consistencia
Ld↓ frente a neta + σT⁴ no equivale a calibración ni cambia silenciosamente el dato.
La prueba de laboratorio y los recursos de la mini-PC siguen siendo aceptación
externa: una simulación no los certifica.

## Ampliación 2: observatorio (especificada el 2026-11-06)

Origen: [propuestas de funcionalidades](../propuestas-funcionalidades.md),
validadas con el usuario. Fuera de alcance: cambiar direcciones Modbus desde la
web, pirheliómetro, test de cierre, RS-485 del SOLYS2 y ceilómetro.

| ID | Requisito | Aceptación |
|---|---|---|
| R11 | Almacenamiento tipado (esquema 2) | Una columna por magnitud y por palabra original; sin JSON por muestra; migración 1→2 con copia previa y verificación campo por campo; esquema desconocido rechazado |
| R12 | Promedios de minuto persistidos | La adquisición guarda cada minuto cerrado en la misma transacción que el ciclo; iguales al CSV `minute_avg`; minutos parciales marcados; se conservan siempre |
| R13 | Eventos de adquisición | Inicio/fin de corrida, puerto perdido/recuperado, sensor sin respuesta/recuperado, cambio de flags y de modo, borrado de errores, reloj y archivo; en la misma transacción que el ciclo; cambios de flags con límite de frecuencia sin perder el estado final |
| R14 | Sincronización del reloj | Consulta SNTP periódica fuera del ciclo de lectura; desfase, retardo, servidor y estado como evento y en el estado de la corrida; nunca ajusta el reloj ni las marcas; aviso con desfase mayor al umbral, sin referencia o sin comprobación reciente |
| R15 | Base del observatorio | SQLite aparte escrita solo por la web: versiones de configuración y bitácora, ambas de solo agregar; anular no borra; la base de mediciones sigue sin rutas de escritura |
| R16 | Configuración versionada | Sitio (lat, lon, altura), SOLYS2 en uso, bola de sombra y etiqueta por dirección, umbrales del proyecto; vigencia desde una fecha (retroactiva exige nota); cada muestra se interpreta con la versión vigente en su instante; cada cambio crea entrada de bitácora |
| R17 | Topología dinámica | Instrumentos, colores y comparaciones salen de la configuración y de la corrida; ninguna dirección fija en UI ni API; comparar solo magnitudes iguales |
| R18 | Posición solar | Algoritmo vectorizado con refracción; cenit y acimut dentro de 0,02° del vector de referencia NREL SPA; distancia Tierra-Sol; sin sitio, las pruebas solares dan "no aplica" |
| R19 | Control de calidad | Límites BSRN físicamente posibles y extremadamente raros (GHI, DHI, Ld↓), cociente difusa/global BSRN, prueba de sombreado y comparación entre iguales (decisiones del proyecto, ajustables); resultado ok/falla/no aplica con motivo, en 1 s y 1 min; nunca modifica valores |
| R20 | SOLYS2 opcional | Con el seguidor inactivo, el piranómetro con bola se presenta como "sombra no garantizada", sin DHI, cociente, sombreado ni DNI estimada; con el seguidor en uso y sombreado fallido, aviso visible |
| R21 | DNI estimada | (GHI − DHI)/cos z con cenit < 80°, SOLYS2 en uso y ambos válidos en el mismo ciclo; etiquetada como derivada |
| R22 | Consulta por fecha y exportación | Consultas entre corridas por intervalo UTC; CSV en streaming (1 s y 1 min) y ZIP con metadatos, bitácora, eventos y LEEME; inválidas incluidas o excluidas; límites de intervalo; lee base activa y archivo sin duplicar |
| R23 | Figuras a pedido | PNG de matplotlib para un intervalo, con pie de metadatos, sin almacenarse; PNG de cada gráfico desde el navegador |
| R24 | Archivo Parquet y retención | Un Parquet por día UTC cerrado (zstd, metadatos de esquema y conversión); manifiesto con filas y SHA-256; borrado de la base activa solo de días más antiguos que la retención (30 días por defecto), tras re-verificar archivo y conteo, en lotes que no retrasan el ciclo |
| R25 | Protección local | `Host` local obligatorio; escrituras con `Origin` igual al host y JSON; la web se niega a escuchar fuera de localhost sin autenticación, salvo contenedor publicado en loopback |
| R26 | Interfaz del observatorio | Monitor con ventanas 15 min–7 d, cursor sincronizado, zoom, marcas de QC y bitácora; histórico con intervalo, carpet plot y descargas; instrumentos con inventario y edición versionada; bitácora y línea de tiempo; documentación para físicos sin internet |
| R27 | Suite orientada a calidad | Tests por nivel: conversión, reglas de QC, geometría solar, escenarios de falla simulados, almacenamiento, configuración, seguridad, frontend, rendimiento y hardware opcional |

### Casos límite de la ampliación

- C09: migración de BD vacía, con corridas interrumpidas, con muestras fallidas y
  con escala desconocida; migración repetida; espacio insuficiente; copia previa.
- C10: minuto cruzado por pérdida de puerto, primer y último minuto parciales,
  corrida que termina a mitad de minuto, cambio de día UTC.
- C11: flags que alternan cada segundo, sensor mudo intermitente, puerto perdido
  al cerrar la corrida, eventos pendientes durante el cierre.
- C12: servidor NTP inaccesible, respuesta inválida o de otro modo, desfase
  grande, retardo alto, verificación deshabilitada.
- C13: configuración sin sitio, latitud o longitud fuera de rango, vigencia
  futura, retroactiva sin nota, dos versiones con la misma vigencia, dirección
  inexistente, bola de sombra en pirgeómetro, SOLYS2 activo sin piranómetro con bola.
- C14: noche, sol bajo el horizonte, cenit 75°/80°/93° exactos, GHI ≤ 50 W/m²,
  valores negativos nocturnos, ciclo sin pareja, varias GHI en el mismo ciclo.
- C15: día archivado que recibe filas tardías (reloj corregido), archivo
  alterado o ausente antes de podar, intervalo que cruza base activa y archivo,
  adquisición detenida a mitad de la poda.
- C16: pedido con `Host` ajeno, escritura sin `Origin` o con `Origin` ajeno,
  contenido no JSON, autor vacío, textos excesivos, anulación repetida.

## Ampliación 3: bajo consumo en la mini-PC (especificada el 2026-11-07)

Origen: la mini-PC se satura con Chrome abierto y empeora con el tiempo; la
terminal muestra trazas JSON difíciles de leer. Decisiones del usuario: gráficos
de 1 minuto como máximo, una interfaz alternativa liviana en Python sin
navegador (Tkinter), sin SOLYS2 ni bola de sombra, y salida de terminal legible.
La versión Svelte se mantiene.

| ID | Requisito | Aceptación |
|---|---|---|
| R28 | Monitor Svelte de 1 minuto | El monitor muestra solo los últimos 60 s (≤ 60 puntos por serie a 1 s); sin ventanas largas; los gráficos del histórico nunca dibujan más de ~600 puntos por serie: el servidor reduce por tramos conservando mínimo y máximo y marca los tramos vacíos como huecos |
| R29 | Ventana liviana Tkinter | `smp22x ventana` sin navegador ni servidor web: gráficos de onda corta (#3, #4) y onda larga (#2) de 60 s actualizados cada 1 s, tabla de últimos valores y filtros para guardar CSV (1 s o 1 min, inválidas incluidas o no, instrumentos, intervalo); columnas clásicas `raw_1s` / `minute_avg`, sin rol ni QC; minimizada no dibuja y al restaurarse redibuja el último minuto desde la base; no importa pandas, numpy ni matplotlib; pyarrow solo al exportar días archivados |
| R30 | Terminal legible | Una línea por ciclo con hora, valores con coma decimal, estado de cada sensor y duración; eventos como líneas `INFO`/`AVISO`/`ERROR` en español; resumen cada N s opcional; errores inesperados en una línea con código de traza; las trazas JSON completas van a archivos rotativos por proceso (`data/logs/`); `SMP_LOG_FORMAT=json` restaura el formato anterior; la web no escribe una línea por pedido |
| R31 | Consumo medido | Medición reproducible de CPU y memoria de la ventana liviana y del navegador con la versión Svelte; prueba de permanencia del monitor Svelte sin crecimiento sostenido de memoria |

### Casos límite de la ampliación 3

- C17: ventana con BD inexistente, esquema 1, adquisición detenida (lecturas
  viejas), sensor mudo, minimizar y restaurar, redimensionar, cerrar la ventana
  con la adquisición activa.
- C18: exportación con intervalo invertido o fuera de límite, sin filas, que
  cruza días archivados, cancelada por error de escritura (no deja archivo a medias).
- C19: consola con timeouts repetidos, puerto perdido, eventos durante el
  cierre, archivo de trazas rotado, formato json, terminal de Windows.
- C20: histórico con tramos vacíos, picos aislados dentro de un tramo, menos
  puntos que el límite (sin reducir).

Los umbrales BSRN provienen de Long y Dutton (2010), *BSRN Global Network
recommended QC tests, V2.0*. La prueba de sombreado, la comparación entre
iguales y sus umbrales son decisiones del proyecto. La comprobación con
hardware real, el SOLYS2 físico y los recursos de la mini-PC siguen siendo
aceptación externa.
