# Entregas y evidencia SDD

Actualizar este archivo en cada entrega. Marcar solo lo verificado.

- [x] Especificación, diseño y casos límite escritos antes de implementar.
- [x] Result, errores y decorador de trazas; configuración .env validada.
- [x] SQLite: esquema, escritor único, palabras originales, lectura y exportación.
- [x] Integración de adquisición, cierre y persistencia de fallos.
- [x] FastAPI: histórico, estado, agregados, SSE y assets estáticos.
- [x] SvelteKit/Bun: interfaz, gráficos y reconexión.
- [x] Make nativo y Docker, .env.example, imágenes y Compose (validación Linux).
- [x] Tests de regresión, almacenamiento, API y frontend; build de producción.
- [x] Prueba de punta a punta simulada y documentación actualizada.
- [ ] Validación de hardware SGR4 y recursos reales de mini-PC Windows (externa).

## Ampliación 2: observatorio (R11–R27, C09–C16)

- [x] Especificación y diseño escritos antes de implementar (2026-11-06).
- [x] Esquema 2 tipado, minutos persistidos, eventos y migración 1→2.
- [x] Reloj SNTP y eventos de reloj.
- [x] Archivo Parquet, manifiesto y retención.
- [x] Posición solar, QC BSRN y pruebas del proyecto.
- [x] Simulador con geometría solar, sombreado y fallas inyectables.
- [x] Base del observatorio: configuración versionada y bitácora.
- [x] API: series, minutos, instrumentos, línea de tiempo, exportación, figuras y protección local.
- [x] Frontend: monitor, histórico, instrumentos, bitácora y documentación.
- [x] Documentación para físicos y documentos técnicos actualizados.
- [x] Suite completa ejecutada y evidencia registrada (2026-11-07).
- [ ] Validación con hardware, SOLYS2 físico y mini-PC (externa).

### Evidencia de la ampliación 2, 2026-11-07

| Requisito / casos | Evidencia ejecutada |
|---|---|
| R11, C09 | `test_storage_v2`: columnas tipadas sin JSON, palabras originales ida y vuelta, < 320 B/fila (medido ~217); migración sin pérdida con copia previa, minutos recalculados, segunda migración sin efecto, lock y esquema desconocido |
| R12, C10 | Minutos persistidos iguales al CSV `minute_avg`; minuto parcial marcado; cruce de días UTC en los escenarios |
| R13, C11 | `EventTrackerTests`: arranque con flags, flags alternando limitados a 1 cada 10 s con estado final correcto, desecante como aviso, silencio y recuperación, modo; eventos de puerto, inicio/fin y corrida interrumpida |
| R14, C12 | `test_clock`: servidor SNTP falso con desfase de 2,5 s, respuestas inválidas (modo, estrato, origen), servidor mudo, eventos y aviso en `/status`, comprobación deshabilitada; el reloj del sistema no se toca |
| R15–R17, C13 | `test_observatory_api`: versiones de solo agregar con entrada de bitácora, vigencia retroactiva sin nota y futura rechazadas, validaciones y avisos; roles en filas de API según configuración; comparación solo entre iguales |
| R18 | Vector NREL SPA (cenit 50,11159° frente a 50,11162°; acimut 194,3426° frente a 194,3402°), simetría alrededor del mediodía, noche y distancia Tierra-Sol |
| R19–R21, C14 | `test_quality`: cada límite BSRN en su borde, cociente en 75°/93° y GHI 50, sombreado que el cociente no ve, comparación, DNI < 80°; propiedades con `hypothesis`: el QC nunca cambia valores |
| R19–R21 (escenarios) | `test_scenarios`: día limpio sin fallas diurnas; bola desalineada y seguidor detenido detectados (> 80 %); SOLYS2 inactivo sin pruebas de difusa; cambio de configuración a mitad del día; offset nocturno −8 W/m²; domo sucio detectado al cuarto día; desnivel con asimetría mañana/tarde; desecante; VOID_DATA; sensor mudo sin rellenar huecos; QC sobre minutos |
| R22–R23 | CSV de 1 s con todas las filas y columnas de QC, CSV de minutos con exclusión de inválidas, ZIP con metadatos, bitácora, eventos y LEEME; límites de intervalo; estimación; PNG a pedido sin archivos en disco |
| R24, C15 | Parquet con metadatos; poda solo de días vencidos y verificados; consulta idéntica antes y después de podar; archivo alterado bloquea la poda; filas tardías se unen y se vuelven a podar; día abierto no se archiva |
| R25, C16 | Host ajeno y nombre de rebinding rechazados; escrituras sin Origin, con Origin ajeno o no JSON rechazadas; sin rutas de escritura en mediciones; `dashboard` se niega a escuchar en red salvo contenedor en loopback |
| R26 | `svelte-check` sin errores ni avisos; 13 tests Bun (alineación, huecos, ciclos, roles, documentación); 8 tests Playwright: monitor, configuración SOLYS2/bola por la UI, bitácora con anulación, histórico con carpet y descargas, documentación, origen ajeno, error con traza y 390 px en las cinco páginas |
| R27 | Rendimiento con un día a 1 s (259 200 filas): 1 h en < 2 s, serie de 1 h por API < 5 s, minutos del día < 3 s, CSV del día < 120 s; adquisición con la web consultando sin ciclos perdidos (≤ 3) |

Suites: 164 tests Python (5 omitidos: hardware y 30 días, que se activan con
`SMP_HARDWARE=1` y `SMP_PERF=1`), 13 Bun y 8 Playwright. La imagen Docker de
tests se construyó (check, tests Bun y build con `docs/fisicos` incluidos) y
ejecutó los 164 tests Python dentro del contenedor. Compose completo no se
levantó en esta sesión.

Revisión visual con la demo simulada (`scripts/e2e_server.py`) en el navegador:
encontró y se corrigieron una lectura válida aislada que no se dibujaba, el
cociente DHI/GHI graficado de noche, la carga del carpet plot antes de tener la
lista de instrumentos, `index.html` cacheado tras actualizar y la elección de
corridas del ZIP por hora de inicio en lugar de por datos.

### Pendiente y alcance real de la ampliación 2

- Todo lo anterior se probó con simulador y datos sintéticos. Falta hardware
  real (SGR4 data model 102, SOLYS2 físico con bola de sombra) y la mini-PC.
- Los umbrales del proyecto (sombreado, comparación) no se validaron con
  mediciones; son ajustables desde la interfaz. El de reloj (1 s) coincide con
  el manual de operaciones BSRN V2.1 §2.3.1, agregado a `references/`.
- Las pruebas BSRN de onda larga que dependen de la temperatura del aire no se
  aplican: no hay sensor de aire.
- La migración se probó con bases de esquema 1 construidas en el test, no con la
  base real del laboratorio: hacer `backup` antes de migrar.

## Ampliación 3: bajo consumo (R28–R31, C17–C20)

- [x] Especificación y diseño escritos antes de implementar (2026-11-07).
- [x] Consola legible y trazas a archivo rotativo.
- [x] Ventana liviana Tkinter con exportación CSV.
- [x] Monitor Svelte de 1 minuto e histórico reducido.
- [x] Tests, medición de consumo y documentación (2026-11-07).
- [ ] Medición en la mini-PC Windows con `scripts/medir_consumo.py` (externa).

### Evidencia de la ampliación 3, 2026-11-07

| Requisito / casos | Evidencia ejecutada |
|---|---|
| R28, C20 | Playwright: el monitor dice «Último minuto», no tiene botones de ventanas largas y la serie del último minuto trae ≤ 61 puntos por sensor; el histórico pide `max_points=600` y recibe series reducidas. `test_low_resource`: serie corta sin tocar, ≤ 600 puntos conservando un pico aislado y un mínimo, tramos vacíos como huecos, `/api/series` con y sin reducción y mismo máximo |
| R29, C17, C18 | `test_ventana` (21): sin imports de pandas/numpy/matplotlib/pyarrow/fastapi; BD inexistente y esquema 1; solo el último minuto; adquisición detenida («Anterior»); sensor mudo; CSV de 1 s y 1 min con columnas clásicas, filtros de inválidas e instrumentos, límites, sin archivo a medias tras un error, mismo CSV antes y después de podar días al Parquet; interfaz oculta: tabla y gráficos llenos, minimizada no consulta y al restaurar redibuja, error de BD mostrado, CSV guardado desde el formulario, validación de fechas. Revisión visual de los gráficos exportados a PostScript |
| R30, C19 | `test_low_resource`: formato de números, línea de ciclo con «sin respuesta» e «inválida», eventos AVISO/ERROR/INFO, resumen cada N s, adquisición sin JSON en la terminal, modo resumen, consola solo con errores inesperados y HTTP 5xx, JSON completo en archivo, nivel WARNING solo fallos y sin serializar lo descartado, modo json como antes |
| R31 | `scripts/medir_consumo.py` en la PC de desarrollo (Linux, 60 s): ventana liviana 0,3 % de CPU media y 27 MB; adquisición simulada 0,2 % y 82 MB; Chromium headless con el monitor Svelte de 1 minuto 2,5 % y 516 MB en 6 procesos. Permanencia del monitor Svelte, 10 min con `--enable-precise-memory-info`: heap JS entre 4,3 y 24,9 MB en diente de sierra (recolección de basura); los mínimos pasan de ~4–5 MB al inicio a ~6–8 MB al final, sin crecimiento sostenido a esta escala. Una primera corrida sin esa opción daba 10,0 MB fijos (valor redondeado por Chrome) y se descartó |

Suites: 198 tests Python (5 omitidos: hardware y 30 días), 13 Bun y 8
Playwright (más la prueba de permanencia, opcional con `SOAK_MINUTES`). En la
imagen Docker de tests, sin Tk, corren 193 y los de la interfaz de la ventana se
omiten (la imagen no usa la ventana).

Pendiente: repetir la medición en la mini-PC con la ventana visible y con Chrome
real (no headless), y la prueba de permanencia de 60 minutos allí.

Trazabilidad: R01–R10 y C01–C08 se definen en [spec.md](spec.md). Los nombres de
tests y las limitaciones pendientes se consignarán aquí al cerrar la implementación.

## Evidencia de aceptación, 2026-10-30

| Requisito / casos | Evidencia ejecutada |
|---|---|
| R01, C07 | Test de adquisición sin web; Compose simulado con dos procesos. Con web detenida el cursor subió de 90 a 210; tras reiniciarla, API y HTML respondieron 200 |
| R02, C02 | `test_wal_concurrent_reader_and_originals`, `test_cycle_rollback_on_duplicate`, `test_only_one_writer_and_lock_released`, `test_busy_is_typed` |
| R03, C03 | Originales U16 conservados; tests de bloque incompleto, signo, escalas y SGR4 simulado, junto a regresiones existentes |
| R04–R05, C01/C04/C05 | Tests de filtros/validación, BD ausente, esquema desconocido, cursor, ventana reciente, inserción tardía, SSE con Last-Event-ID y rechazo de cursor ajeno |
| R06, C06/C07 | `svelte-check`: sin errores ni warnings; Bun tests de deduplicación, buffer, huecos y baja irradiancia; Playwright con API/adquisición reales, recarga y viewport 390 px sin desborde |
| R07 | Tests de promedios de irradiancia/Ld↓, inválidos retenidos y excluidos, y derivadas con pares válidos; guía de tratamiento con ecuaciones explícitas |
| R08 | `test_trace_chain_and_typed_result`, respuestas con trace_id, muestra fallida explícita y fallo de persistencia que detiene la corrida |
| R09, C08 | Build Docker multietapa y Compose, configuración renderizada, precedencia env/CLI, helper nativo, skill local validada con quick_validate |
| R10, C02/C08 | Exportación CSV desde SQLite y backup incluyendo WAL; muerte abrupta de proceso conserva corrida y libera lock; nuevo escritor marca `interrupted` |

La prueba adicional de permisos ejecutó ambos contenedores con UID/GID del
usuario, consultó desde Python nativo la BD activa (cursor 24) y cerró Compose.
Esto evita archivos de root en el modo alternado Linux. `make docker-up` prepara
la carpeta local; una ruta de datos personalizada debe existir y pertenecer al
UID/GID configurado. No extrapolar esta prueba a Docker Desktop Windows.

Comandos de reproducción: [operación](../operacion-dashboard.md). Suites: 56 tests
Python, 5 tests Bun y 2 tests Playwright. La imagen de tests ejecuta la suite
Python y su build ejecuta checks, tests Bun y compilación del frontend.

## Pendiente y alcance real

- Hardware SGR4 data model 102, puerto COM y consumo/latencias en Windows real.
- Corrida sostenida de horas y prueba de corte físico de energía. Muerte de
  proceso es una simulación de fallo distinta de pérdida de energía.
- «Disco lleno» se inyecta como fallo de almacenamiento: no se llenó el disco del
  usuario. La propagación y detención están probadas, no todos los fallos posibles
  de cada sistema de archivos o controlador.
- El modo mixto adquisición Windows/web Docker depende de coherencia del bind
  mount y locks de la instalación concreta; recomendar ambos procesos nativos si
  no se valida. USB en contenedor se documenta para Linux, sin prueba física aquí.
- Alertas científicas con umbrales justificados, anotaciones y navegación temporal
  completa no forman parte de esta interfaz de lectura. La API sí filtra fechas;
  la UI muestra el tramo reciente de cada corrida. Promedios están en API/CSV,
  todavía no superpuestos en las gráficas vivas.

Los casos C01–C08 son un catálogo de riesgos para mantener, no una afirmación de
que todas sus variantes y plataformas ya se hayan ensayado.
