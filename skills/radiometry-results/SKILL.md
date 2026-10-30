---
name: radiometry-results
description: Aplicar errores tipados y trazas al modificar las fronteras de adquisición, SQLite y API de este proyecto de radiometría.
---

Leer el requisito afectado en `docs/sdd/spec.md` antes de cambiar una frontera.
Actualizar su evidencia en `docs/sdd/tasks.md` tras verificarla.

Usar `Ok[T] | Err` de `smp22x/result.py` para fallos esperados del repositorio;
el discriminante es `ok` y `Failure` define código, mensaje y retryable. Extender
la unión de códigos cuando haya un fallo nuevo que necesite una decisión distinta.
Mantener valores directos en conversiones puras. `Sample` conserva fallo de
lectura y valores ausentes; no fabricar una muestra cero para ocultar un error.

Aplicar `@traced` de `smp22x/tracing.py` a fronteras nuevas. Incluir solo contexto
explícito útil como run_id, ciclo y dirección. Conservar trace_id y cadena anidada;
no volcar argumentos completos, .env ni secretos. En una función async conservar
ContextVar y no usar directamente el decorador sync para envolver la coroutine.
Errores esperados se clasifican; errores inesperados conservan traceback en JSON
y el navegador recibe un mensaje seguro con trace_id.

Un error de persistencia no puede convertirse en éxito. No publicar muestras
antes del commit. Probar el fallo y su consecuencia observable, no solo el tipo
de retorno. Al agregar una operación, elegir casos de C01–C08 pertinentes y
anotar lo que requiere hardware real. No afirmar cobertura total por pasar tests.
