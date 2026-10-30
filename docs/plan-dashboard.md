# Dashboard: implementación y evolución

Stack implementado: **adquisición Python independiente + SQLite WAL + FastAPI +
SvelteKit/TypeScript con Bun**. [Operación](operacion-dashboard.md) contiene los
comandos; [C4](arquitectura-c4.md) describe los componentes actuales; el
[SDD](sdd/spec.md) registra requisitos y casos límite.

## Entregado

| Área | Implementación |
|---|---|
| Persistencia | SQLite por ciclo, esquema versionado, lock de escritor, valores físicos y palabras originales |
| Consulta | API de solo lectura, filtros UTC, límites, cursor, últimas muestras y agregados por minuto |
| Vivo | SSE con IDs después de commit y reconexión; histórico inicial acotado |
| Frontend | Selección de corrida, tabla de calidad/edad y seis gráficas SVG locales |
| Gráficas | Onda corta, Ld↓ y comprobación, diferencia relativa, temperatura, RTT y alimentación |
| Calidad | Lecturas inválidas/fallidas conservadas; datos obsoletos visibles; huecos sin interpolar |
| Diagnóstico | Result en fronteras, códigos explícitos, trazas JSON anidadas y trace_id en API |
| Operación | .env.example, Make nativo/Docker, Compose simulado y variante Linux serie |
| Análisis | CSV/PNG anteriores y exportación desde SQLite; backup consistente en vivo |

La implementación utiliza SVG con buffer acotado en lugar de añadir uPlot.
Los promedios se consultan por API y se conservan en CSV; la gráfica viva de esta
versión muestra muestras, sin superponer puntos de promedio de minuto. La UI
consulta el tramo reciente de cada corrida; intervalos históricos arbitrarios
se recuperan por API/exportación. Se documentan esos límites para no presentar
una función planeada como ya entregada.

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
- Justificar umbrales de alertas según la investigación. Las referencias de
  2 %, ±5 W/m² y 10 V no son límites universales del fabricante.
- Anotaciones y controles desde la web quedan fuera de la interfaz de lectura;
  requerirían una nueva especificación de escrituras y su coordinación.
- Añadir navegación temporal más amplia o promedios visuales si el uso real lo
  necesita; evitar cargar todo el histórico en el navegador.

La evidencia realizada y lo pendiente se mantienen en [SDD/tasks](sdd/tasks.md).
Fuentes de diseño: [SQLite WAL](https://sqlite.org/wal.html),
[SQLite Backup](https://sqlite.org/backup.html),
[FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/),
[SvelteKit estático](https://svelte.dev/docs/kit/adapter-static),
[Bun](https://bun.sh/docs),
[InfluxDB 3 Core](https://docs.influxdata.com/influxdb3/core/install/).
