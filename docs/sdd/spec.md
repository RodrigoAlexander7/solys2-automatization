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
