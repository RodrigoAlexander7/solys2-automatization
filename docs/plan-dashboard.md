# Dashboard: implementación y evolución

Stack implementado: **adquisición Python independiente + SQLite WAL + FastAPI +
SvelteKit/TypeScript con Bun**. [Operación](operacion-dashboard.md) contiene los
comandos; [C4](arquitectura-c4.md) describe los componentes actuales; el
[SDD](sdd/spec.md) registra requisitos y casos límite.

## Entregado

| Área | Implementación |
|---|---|
| Persistencia | Esquema 2 tipado (~217 B/fila), minutos y eventos en la misma transacción del ciclo, migración verificada desde el esquema 1 |
| Archivo | Parquet diario verificado (filas y SHA-256), retención de 30 días para 1 s, minutos y eventos siempre |
| Consulta | Por corrida y por fecha entre corridas; base activa + Parquet sin duplicar |
| Vivo | SSE con IDs después de commit y reconexión; monitor limitado al último minuto |
| Observatorio | Configuración versionada (sitio, SOLYS2, bola de sombra, umbrales) y bitácora de solo agregar, en base aparte escrita por la web |
| Calidad | Validez del sensor, QC BSRN, sombreado y comparación entre iguales; DNI estimada; nunca modifican valores |
| Tiempo | Comprobación SNTP del reloj con eventos y avisos; sin ajustar el reloj |
| Frontend | Monitor del último minuto, histórico (series reducidas, carpet plot y descargas), instrumentos, bitácora con línea de tiempo y documentación para físicos |
| Interfaz liviana | Ventana Tkinter sin navegador: último minuto, últimos valores y CSV clásico (~30 MB de RAM) |
| Terminal | Una línea legible por ciclo o resumen cada N s; trazas JSON en `data/logs/` |
| Gráficas | uPlot con cursor sincronizado, zoom, marcas de QC y bitácora, PNG por gráfico; figuras matplotlib a pedido |
| Seguridad | Host local obligatorio, escrituras con Origin propio y JSON, negativa a escuchar en red sin autenticación |
| Diagnóstico | Result en fronteras, códigos explícitos, trazas JSON anidadas y trace_id en API |
| Operación | .env.example, Make nativo/Docker, Compose simulado y variante Linux serie, `migrate` y `archive` |
| Tests | Por niveles, incluidos escenarios de falla simulados, rendimiento y hardware opcional |

## Decisiones operativas

Un segundo proceso Python protege la continuidad frente a reinicios de la web.
No requiere dos instalaciones ni Docker: un lanzador puede iniciar ambos. La
adquisición nunca depende de FastAPI ni de SvelteKit.

SQLite reduce servicios y admite lectores mientras se escribe con WAL; el
refresco de 1 s es de presentación, no un mecanismo de bloqueo. Los procesos
comparten disco local. No usar SMB ni carpetas sincronizadas para la BD activa.

SvelteKit se compila a assets estáticos y FastAPI los sirve; Bun no se ejecuta
en producción. Las novedades son unidireccionales, por lo que se usa SSE.
WebSocket no convertiría automáticamente una consulta de BD en un evento.

InfluxDB se reconsiderará según volumen, recursos y consultas medidas. Su edición
3 Core ofrece binario Windows, así que Docker no es obligatorio por definición.
No se migra solo porque los datos sean temporales.

## Aceptación y trabajo posterior

- Confirmar con hardware real los registros SGR4 del data model 102 y los códigos
  observados. La fórmula de consistencia no certifica el mapa completo.
- Ejecutar una prueba sostenida en la mini-PC Windows: CPU, RAM, disco, latencia
  de commit, refresco visual y ciclos perdidos frente a la adquisición sola.
- Validar con mediciones reales los umbrales del proyecto (sombreado kt > 0,6 y
  DHI/GHI > 0,8; comparación 5 %). No son límites del fabricante. El de reloj
  (1 s) sigue el manual de operaciones BSRN V2.1 §2.3.1.
- Integrar el SOLYS2 por RS-485 o Ethernet con su protocolo de comandos
  (manual en `references/solys2-sun-traker/`, anexos A–D): posición, hora GPS y
  estado de seguimiento. Luego el pirheliómetro con el test de cierre de BSRN
  (§9.3.2.1: 2 % o 15 W/m²) y, si corresponde, el ceilómetro.
- Agregar autenticación antes de abrir el dashboard a la red del laboratorio.
- Cambiar direcciones Modbus sigue fuera de la web (SmartExplorer).

La evidencia realizada y lo pendiente se mantienen en [SDD/tasks](sdd/tasks.md).
Fuentes de diseño: [SQLite WAL](https://sqlite.org/wal.html),
[SQLite Backup](https://sqlite.org/backup.html),
[FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/),
[SvelteKit estático](https://svelte.dev/docs/kit/adapter-static),
[Bun](https://bun.sh/docs),
[InfluxDB 3 Core](https://docs.influxdata.com/influxdb3/core/install/).
