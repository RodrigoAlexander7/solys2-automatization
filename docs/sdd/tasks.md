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
