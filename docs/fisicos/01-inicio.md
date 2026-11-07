# Cómo usar el dashboard

El dashboard muestra lo que registra la adquisición. Cerrarlo no detiene las
mediciones: la adquisición es otro proceso y guarda cada ciclo en la base local
antes de que la web lo vea.

Hay dos interfaces sobre los mismos datos:

| Interfaz | Cómo se abre | Para qué |
|---|---|---|
| **Ventana liviana** | `python -m smp22x ventana` (o `make ventana`) | Uso diario en la mini-PC: sin navegador, unos 30 MB de RAM |
| **Dashboard completo** | http://127.0.0.1:8050 en el navegador | Configuración, bitácora, control de calidad, histórico y documentación |

## Ventana liviana

Muestra el último minuto de onda corta (#3 y #4) y de onda larga (#2), una tabla
con la última lectura de cada sensor y un formulario para guardar un CSV.

- Se actualiza una vez por segundo. **Minimizarla no pierde datos**: los guarda
  la adquisición; al restaurarla vuelve a leer el último minuto de la base.
- Cerrarla tampoco detiene la adquisición.
- No considera el SOLYS2 ni la bola de sombra: #3 y #4 se muestran como
  piranómetros. Para difusa, control de calidad o bitácora use el dashboard completo.
- **Descargar datos**: escriba desde/hasta como `AAAA-MM-DD HH:MM` (hora local) o
  use los atajos; elija 1 s (hasta 31 días) o 1 min (hasta 366), los instrumentos
  y si incluir lecturas inválidas, y pulse **Guardar CSV…**. Las columnas son las
  clásicas de `raw_1s.csv` o `minute_avg.csv`, sin rol ni QC.

Con `make run-ligero` se inician juntas la adquisición y la ventana.

## Secciones

| Sección | Para qué |
|---|---|
| **Monitor** | Última lectura de cada instrumento, alertas, comparaciones y gráficos en vivo |
| **Histórico y descargas** | Elegir un intervalo, ver series y carpet plot, descargar CSV, ZIP o una figura PNG |
| **Instrumentos** | Identificación leída de cada equipo, bola de sombra, SOLYS2, sitio y umbrales |
| **Bitácora y eventos** | Anotar limpiezas, nivelaciones, sombras o mantenimiento y ver la línea de tiempo |
| **Documentación** | Estos textos |

## Monitor (dashboard completo)

- Muestra **solo el último minuto**, con muestras de 1 s en vivo. Así el
  navegador dibuja pocos puntos y no se satura con el tiempo. Para intervalos
  más largos use **Histórico y descargas**: ahí cada serie se reduce a unos 600
  puntos para dibujar (mínimo y máximo de cada tramo, así los picos se ven), y
  la descarga trae siempre todas las filas.
- **Gráficos**: arrastre horizontalmente para ampliar y haga doble clic para
  volver. El cursor se sincroniza entre gráficos y la leyenda muestra el valor
  en el instante señalado. **PNG** descarga ese gráfico tal como se ve.
- **Cruces rojas**: alguna prueba de calidad falló en ese punto (ver
  [calidad de datos](03-calidad-de-datos.md)).
- **Líneas punteadas verticales**: notas de la bitácora.
- **Huecos**: un tramo sin línea significa que no hubo lectura válida. Nunca se
  rellena con ceros ni se interpola.

## Estados de una lectura

| Estado | Significado |
|---|---|
| VÁLIDA | El sensor respondió en modo normal y sin flags que invaliden el dato |
| INVÁLIDA | Respondió, pero en otro modo o con flags como VOID_DATA u OVERFLOW |
| SIN RESPUESTA | Timeout o error de comunicación; la lectura se guarda como fallida |
| ANTERIOR | La última lectura tiene más de unos segundos: la adquisición pudo detenerse |

## Descargar datos o figuras

En **Histórico y descargas** elija un intervalo y pulse:

- **Descargar CSV**: una tabla; con 1 s hasta 31 días y con 1 min hasta 366.
- **Descargar ZIP con metadatos**: el CSV más `metadata.json` (instrumentos,
  configuraciones vigentes, comprobaciones de reloj, definición de pruebas),
  `bitacora.csv`, `eventos.csv` y un `LEEME.txt`.
- **Figura PNG**: se genera al pedirla con el pie de metadatos y no se guarda.

Detalle de columnas en [datos, exportación y archivo](07-datos-exportacion-y-archivo.md).

## Registrar algo en la bitácora

En **Bitácora y eventos** indique su nombre, la categoría, los instrumentos
afectados y una nota. Si lo anota más tarde, ponga la hora real del hecho.
Una nota no se borra: se **anula** con un motivo y queda visible tachada.

## SOLYS2 y bola de sombra

En **Instrumentos** se marca qué piranómetro tiene la bola de sombra y si el
SOLYS2 está en uso. Al guardar se crea una versión nueva con fecha de vigencia y
una entrada automática en la bitácora. Ver [SOLYS2 y sombreado](04-solys2-y-sombreado.md).
