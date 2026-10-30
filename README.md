# Adquisición SMP22x + SGR4 por RS-485 / Modbus RTU

Lectura de tres radiómetros smart de **Kipp & Zonen** conectados en paralelo a
un **PMU485 Smart Setup Hub** (0382460) enchufado por USB al PC:

| Dirección Modbus | Sensor | Mide |
|---|---|---|
| **2** | Pirgeómetro **SGR4** | Radiación de onda larga descendente Ld↓ (infrarrojo del cielo), W/m² |
| **3** | Piranómetro **SMP22x** (0374960) | Irradiancia global de onda corta, W/m² |
| **4** | Piranómetro **SMP22x** (0374960) | Irradiancia global de onda corta, W/m² |

La adquisición Python guarda las muestras en **SQLite** y produce CSV/PNG
compatibles. Otro proceso **FastAPI** consulta SQLite y sirve el dashboard
**SvelteKit**, instalado y compilado con **Bun**. Cerrar la web no detiene las
mediciones. La UI es de solo lectura.

## Arranque del dashboard

Requisitos: Python 3.13, Bun 1.4 y, opcionalmente, Make o Docker.

```sh
make install
make run
```

Abrir http://127.0.0.1:8050. `make install` copia `.env.example` a `.env` sin
sobrescribirlo; la plantilla usa sensores **simulados**. Para hardware, cambiar
`SMP_SIMULATE=0` y configurar el puerto. Con Docker: `make docker-up` (todo
simulado), `make docker-web` (solo web con adquisición nativa). Las instrucciones
para Windows sin Make están en [operación](docs/operacion-dashboard.md).

### Iniciar el frontend en desarrollo

Desde la raíz del repositorio, ejecutar `make install` una vez y abrir **tres
terminales**. Mantener estos procesos activos:

| Terminal | Con Make | Sin Make, Linux/macOS | Función |
|---|---|---|---|
| 1 | `make acquire` | `.venv/bin/python -m smp22x log --no-plot` | Adquisición → SQLite |
| 2 | `make web` | `.venv/bin/python -m smp22x dashboard` | API FastAPI en http://127.0.0.1:8050 |
| 3 | `make frontend` | `bun run --cwd frontend dev` | Frontend SvelteKit con recarga automática |

**Abrir http://127.0.0.1:5173 para desarrollar el frontend.** Si ese puerto está
ocupado, Vite muestra el puerto alternativo en la terminal. El frontend envía
`/api` a FastAPI en el puerto 8050 mediante el proxy de
[`frontend/vite.config.ts`](frontend/vite.config.ts). Iniciar solo SvelteKit permite
ver la interfaz, pero para consultar datos necesita la API; para generar nuevas
muestras necesita también la adquisición. No ejecutar a la vez `make run` y
esta secuencia, porque `make run` ya inicia adquisición y API.

En **Windows sin Make**, completar primero la instalación de
[operación](docs/operacion-dashboard.md#windows-sin-make). Luego usar tres
terminales PowerShell en la raíz:

```powershell
# Terminal 1: adquisición
.venv\Scripts\python -m smp22x log --no-plot
```

```powershell
# Terminal 2: API
.venv\Scripts\python -m smp22x dashboard
```

```powershell
# Terminal 3: frontend
bun run --cwd frontend dev
```

### Usar el frontend compilado

`make run` ejecuta `make build` y arranca adquisición y API. En este modo se
abre **http://127.0.0.1:8050**, y FastAPI sirve los archivos compilados de SvelteKit;
no hace falta ejecutar `make frontend`. Para reconstruir y arrancar por separado:
`make build`, después `make acquire` y `make web` en terminales distintas.
Docker también sirve el frontend compilado en el puerto 8050 por defecto.

La [especificación SDD](docs/sdd/spec.md), su [diseño](docs/sdd/design.md) y la
[evidencia de tests](docs/sdd/tasks.md) describen requisitos, casos límite y
validación. `make check` y `make test` ejecutan verificaciones de frontend/backend.

### Qué se hace con la medida

Se interpretan signo y escalas de los enteros según el manual para convertirlos
a W/m², °C o V. **La medida principal se expresa como la entrega el sensor en
esas unidades, sin una corrección adicional del programa.** Se guardan también
palabras originales en SQLite. Por separado se calculan promedios, diferencias
y la comprobación SGR `neta + σT⁴`, que nunca sustituye a Ld↓ leído. Véase el
[tratamiento de datos](docs/tratamiento-datos.md).

Documentación oficial en [`references/`](references/):

- [`references/pyranometer-SMP22x/`](references/pyranometer-SMP22x/): hoja de
  datos SMPx 2025 y manual de operación SMP10x/SMP22x V20. Las referencias `§`
  de este README y del código son secciones de ese manual salvo que se indique
  otra cosa. Su apéndice 15 es el mapa de registros común a los sensores smart,
  incluidos los registros "only for SGR".
- [`references/pyrgeometer-SGR4/`](references/pyrgeometer-SGR4/): manual de
  operación CGR4/SGR4 V10 (citado como "manual SGR4") y folleto de
  pirgeómetros. El manual SGR4 no incluye mapa de registros.

Para entender las direcciones y las conversiones, empiece por la
[guía de lectura de registros](docs/lectura-registros.md): contiene significado,
tipo, fórmula, unidad y página del manual para ambos sensores.
El [índice de documentación](docs/README.md) propone un orden de lectura.

Notas del proyecto en [`docs/`](docs/): [decisiones técnicas](docs/decisiones-tecnicas.md),
[arquitectura explicada con C4](docs/arquitectura-c4.md),
[prueba 01](docs/prueba-01.md) y [plan del dashboard en tiempo real](docs/plan-dashboard.md).

## 1. Conexión

El PMU485 es a la vez un conversor USB ↔ RS-485 de 2 hilos (chip FTDI, Windows
lo reconoce como `USB Serial Port (COMx)`) y una fuente de 12 V alimentada por
el USB. Puede alimentar y comunicar hasta 10 radiómetros en paralelo:
50 mA para el primero + 10 mA por cada sensor adicional [1][2].

El SMP22x y el SGR4 usan el mismo código de colores (§7.3.5; manual SGR4 §7.2.2.5):

| Hilo         | Función                   | Borne del PMU485       |
|--------------|---------------------------|------------------------|
| Blanco       | Alimentación 5–30 V DC    | +12 V                  |
| Negro        | Tierra de alimentación    | GND                    |
| Amarillo     | RS-485 Data + [3]         | B / +                  |
| Gris         | RS-485 Data − [3]         | A / −                  |
| Azul         | Común Modbus              | GND / común            |
| Rojo, verde, marrón | No conectados      | Aislar                 |

- El manual V20 no dice cuál de los dos hilos (amarillo o gris) es + y cuál es −. La hoja de
  Kintech para el SMP10 indica amarillo = Data+ y gris = Data− [3]. Si ningún
  sensor responde, intercambie amarillo y gris; hacerlo no daña nada.
- Los tres sensores deben tener **la misma velocidad y paridad** y
  **direcciones distintas** (§11.1). Fábrica: 19200 baud, 8 bits, paridad par,
  1 bit de parada (§8.1; manual SGR4 §8.1). El SGR4 sale de fábrica con la
  dirección 1, así que hay que cambiarla a 2 con SmartExplorer antes de
  conectarlo al bus.
- Con cables largos, ponga una resistencia de 120–150 Ω entre A y B en el
  último sensor del bus (§7.3.5). Con cables de 10 m a 19200 baud normalmente
  no hace falta.
- **No abra SmartExplorer al mismo tiempo que este programa.** El puerto COM
  solo lo puede usar un programa a la vez, y un bus Modbus admite un único maestro.

## 2. Instalación de la CLI

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

## 3. Uso

```bash
.venv\Scripts\python -m smp22x ports
```
Muestra los puertos COM. El PMU485 aparece marcado como FTDI. Con `--port auto`,
que es el valor por defecto, el programa lo elige solo.

```bash
.venv\Scripts\python -m smp22x scan
```
Busca sensores en las direcciones 1–20 a 19200 8E1 e indica el modelo y si es
piranómetro o pirgeómetro. Con `--all-baud` prueba además otras velocidades y
paridades.

```bash
.venv\Scripts\python -m smp22x info --clear
```
Muestra, para cada sensor, el modelo, el número de serie, el modo, los flags de
estado, la medida, la temperatura, la tensión de alimentación, la humedad y la
presión internas y el tiempo de respuesta. `--clear` borra los flags que el
sensor activa siempre al arrancar (§15.8).

En el pirgeómetro muestra la radiación neta, Ld↓ y una **verificación**: Ld↓
calculado como neta + σT⁴ (manual SGR4 §7.1.5) frente a Ld↓ leído. Si difieren
más de 5 W/m², avisa y termina con código 1: se debe investigar la interpretación
de registros, escala y temperatura. La coincidencia es una comprobación de
consistencia, no una calibración ni una demostración completa de compatibilidad
(ver D15 en [decisiones técnicas](docs/decisiones-tecnicas.md)).

**Qué dirección es el pirgeómetro.** Por defecto, la 2. Se cambia con
`--pyrgeometers` en `info` y `log`: `--pyrgeometers 5` si estuviera en la 5, o
`--pyrgeometers` sin valores si no hay ninguno. Si el modelo que reporta el
sensor no coincide con lo configurado, el programa avisa.

```bash
.venv\Scripts\python -m smp22x log -d 30m --label prueba1
```
Adquiere datos durante 30 minutos (`-d 90s`, `-d 2h`… o sin `-d` hasta pulsar
Ctrl+C) y al terminar genera las gráficas.

```bash
.venv\Scripts\python -m smp22x plot data\2026-09-28_120000_prueba1
```
Vuelve a generar las gráficas de una prueba ya guardada.

Añadiendo `--simulate` a `scan`, `info` o `log` se usan sensores simulados sin
hardware: un SGR4 en la dirección 2 y dos SMP22x en la 3 y la 4. Solo responden
a 19200 8E1, como los reales de fábrica. Sirve para probar el programa. Las
carpetas simuladas terminan en `_SIM` y las gráficas lo indican.

Tests backend: `.venv\Scripts\python -m unittest discover -s tests` tras instalar
`requirements-dev.txt`. Frontend: `bun run --cwd frontend check` y
`bun run --cwd frontend test`.

## 4. ¿Cada cuánto leer?

**Recomendación: leer cada 1 s y guardar promedios de 1 minuto.** Es el valor
por defecto (`--interval 1`).

| Criterio | Dato | Fuente |
|---|---|---|
| Actualización del registro de irradiancia | 1 vez por segundo | Kipp & Zonen, *Pyranometer interface options* [4] |
| Tiempo de respuesta del sensor | 63 % < 0,7 s · 95 % < 2 s | Hoja de datos SMPx 2025 |
| Recomendación del fabricante | "muestrear cada segundo y guardar promedios de un minuto" | Manual V20 §9.2 |
| Valor por defecto del software del fabricante | 1 lectura por segundo | Manual SMP (anterior), §4.1 [5] |
| IEC 61724-1, Clase A | muestreo ≤ 3 s, registro ≤ 1 min | [6] |
| Tiempo de respuesta del SGR4 | 63 % < 6 s · 95 % < 18 s | Manual SGR4 §13.1 |

El pirgeómetro es mucho más lento que los piranómetros, así que leerlo cada 1 s
es compatible con un ciclo de 1 s. Las lecturas son secuenciales: compartir
ciclo permite agruparlas, pero sus marcas de tiempo son distintas.

- **Menos de 1 s no aporta información.** El registro solo cambia una vez por
  segundo, así que se leerían valores repetidos y se ocuparía el bus para nada.
  Se puede comprobar con `log -i 0.5`: en `resumen.csv`, la columna
  `repetidos_%` subirá a ~50 %.
- **Más de 3 s** deja de cumplir la Clase A de IEC 61724-1. El programa avisa.
- `IO_STDEV_SENSOR1` es la desviación desde la lectura anterior (§15.6). A 1 s
  sale casi 0. Para ver la variabilidad dentro de cada minuto, use la columna
  `std_wm2` de `minute_avg.csv`.
- Humedad relativa y presión internas: se leen cada 60 s (`--diag-every`), pero
  su interpretación no está validada para el firmware antiguo del montaje.
  No usarlas como diagnóstico fiable hasta confirmar el mapa (guía, sección 5).

### Carga del bus a 19200 baud 8E1 (11 bits por carácter = 0,573 ms)

Cada ciclo hace una lectura FC04 por sensor: 8 registros (2–9) en los
piranómetros y 10 registros (2–11) en el pirgeómetro, que añade Ld↓:

| Parte | Piranómetro | Pirgeómetro |
|---|---|---|
| Petición (8 bytes) | 4,6 ms | 4,6 ms |
| Respuesta (5 + 2N bytes) | 21 B = 12,0 ms | 25 B = 14,3 ms |
| 2 silencios t3.5 | 4,0 ms | 4,0 ms |
| **Total en el cable** | **20,6 ms** | **22,9 ms** |
| + latencia USB del FTDI (16 ms por defecto) + tiempo de respuesta del sensor | ≈ 10–30 ms | ≈ 10–30 ms |

Con paridad `N` el carácter ocupa 10 bits en lugar de 11, porque el programa
usa siempre 1 bit de parada.

Con tres sensores, cada ciclo ocupa unos 60–150 ms. Leyendo cada segundo, el bus
queda ocupado un 6–15 %, así que hay margen de sobra: aunque un sensor no
responda y se espere su timeout de 0,3 s, el ciclo sigue cabiendo en 1 s. El
tiempo real de cada lectura se guarda en `rtt_ms`, y el resumen final indica la
ocupación medida.

Por qué el ciclo es robusto:

- Las peticiones van **en serie**, una detrás de otra. Modbus RTU es semidúplex
  y con un solo maestro, así que nunca se hacen peticiones en paralelo.
- El programa intenta alinear ciclos al segundo. Con intervalo 1 s, un minuto
  completo sin pérdidas tiene 60 muestras por sensor; inicio/fin parciales,
  desconexiones y backoff pueden reducirlas.
- Si un sensor falla 5 veces seguidas, solo se le vuelve a consultar cada 10
  ciclos. Así no se retrasa a los demás.
- Si se desconecta el USB, el programa intenta reabrir el puerto cada 2 s. Esa
  desconexión cuenta en `port_errors` y `port_downtime_s` de `run.json`, no
  como error de comunicación de ningún sensor. El ciclo cortado y los que no se
  hicieron mientras faltaba el puerto tampoco entran en las estadísticas de
  ciclo ni en `overruns`.

### Tiempos de estabilización

- Tras encender, el sensor necesita **hasta 3 h** para alcanzar una temperatura
  estable. En ese tiempo las medidas pueden desviarse (§7.3.5).
- Tras cambiar la dirección o la velocidad, el sensor se reinicia y tarda
  **~1 min** en estar listo (§8.1.3).

## 5. Archivos de salida

Por cada prueba se crea `data/<fecha>_<hora>[_etiqueta]/` con:

La CLI usa además `data/acquisition.sqlite3` por defecto (`--db` o `SMP_DB_PATH`).
Contiene corridas, muestras originales y convertidas, identificación, estado y
errores. El commit de ciclo precede a la escritura de las proyecciones CSV.
Se puede regenerar una carpeta CSV con `python -m smp22x export RUN_ID --out
data/exportacion`. No copiar solo el archivo `.db` abierto: usar `backup`.

- **`raw_1s.csv`**: una fila por sensor y ciclo. Columnas:
  - `time_utc` y `time_local`: marca de tiempo.
  - `cycle`: agrupa lecturas secuenciales; no indica simultaneidad.
  - `address`: dirección del sensor.
  - `sensor_type`: `pyranometer` o `pyrgeometer`.
  - `irradiance_wm2`: `IO_SENSOR1_DATA`. En los piranómetros es la irradiancia
    global compensada en temperatura. **En el pirgeómetro es la radiación neta
    del detector**, normalmente negativa (§15.2).
  - `raw_irradiance_wm2`: `IO_RAW_SENSOR1_DATA`, ya convertido a W/m²; no son
    enteros originales del bus. El CSV no conserva factor ni palabras originales;
    ambos sí se conservan en SQLite.
  - `lw_down_wm2` y `raw_lw_down_wm2`: solo pirgeómetro. `IO_SENSOR2_DATA` e
    `IO_RAW_SENSOR2_DATA`, radiación de onda larga descendente Ld↓.
  - `stdev_wm2`, `body_temp_c`, `supply_v`.
  - `rh_pct` y `pressure_hpa`: solo cada 60 s.
  - `mode`, `status_hex`, `status_flags`.
  - `valid`: 1 si el dato es utilizable.
  - `rtt_ms`: tiempo de respuesta de la lectura.
  - `error`: descripción del fallo, si lo hubo.
- **`minute_avg.csv`**: por minuto y sensor, tipo de sensor, número de
  muestras, número de válidas, media, mínimo, máximo y desviación típica de la
  **magnitud principal**: irradiancia global en los piranómetros y Ld↓ en el
  pirgeómetro. `net_mean_wm2` es la media de la radiación neta (solo
  pirgeómetro). `complete=1` si hay al menos un 80 % de muestras válidas.
- **`run.json`**: configuración, identificación de cada sensor (modelo, número
  de serie, firmware) y estadísticas del bus. El tiempo teórico en el cable se
  calcula con los baudios y la paridad reales del bus, por sensor
  (`per_sensor.*.theoretical_wire_ms_per_read`) y por ciclo
  (`bus_timing.theoretical_wire_ms_per_cycle`).
- **`irradiancia.png`**: solo piranómetros. Tres paneles con la irradiancia a
  1 s (puntos = media de 1 min), la diferencia de cada piranómetro respecto a la
  media de los piranómetros y la temperatura interna.
- **`onda_larga.png`**: solo pirgeómetro. Ld↓ a 1 s con la verificación
  neta + σT⁴ en línea discontinua (deben superponerse), la radiación neta y la
  temperatura interna.
- **`comunicacion.png`**: tiempo de respuesta por lectura, timeouts y tensión de
  alimentación de todos los sensores.
- **`resumen.csv`**: estadísticas por sensor. También se imprime en consola.
  En el pirgeómetro incluye `neta_media_Wm2` y `Ld_menos_calc_Wm2` (media de
  Ld↓ leído menos neta + σT⁴, debería estar cerca de 0).

Las pruebas guardadas antes de este cambio no tienen `sensor_type`: `plot` las
trata como si todos los sensores fueran piranómetros, que es como se leyeron.

### Cuándo una muestra cuenta como válida (`valid = 1`)

Una muestra es válida si el sensor respondió, trae su magnitud principal
(irradiancia o Ld↓), está en modo normal (1) y no tiene activo ninguno de estos
flags: `VOID_DATA`, `OVERFLOW`, `UNDERFLOW`,
`ADC_ERROR`, `CALIBRATION_ERROR` ni `BODY_TEMP_ERROR` (§15.6).

- `ERROR` y `POWER_FAILED` se activan **siempre** al arrancar el sensor (§15.8).
  Por sí solos no invalidan el dato. El programa los borra escribiendo el coil
  `IO_CLEAR_ERROR`, como hace el ejemplo del manual (§15.11). Ese coil no cambia
  la configuración del sensor.
- `DESICCANT_WARNING` indica que hay que cambiar el desecante. Queda registrado,
  pero no invalida el dato.

## 6. Limitaciones y puntos a verificar con el hardware

- La [prueba 01](docs/prueba-01.md) se hizo con hardware real antes de saber
  que la dirección 2 es un pirgeómetro. El soporte del SGR4 solo se ha probado
  con el simulador y con tests unitarios. La siguiente prueba con hardware
  debería ser `ports` → `scan` → `info`, y comprobar en `info` que la
  verificación del pirgeómetro (neta + σT⁴ frente a Ld↓) cuadra.
- El SGR4 de la prueba 01 tiene data model 102 y el manual V20 describe el
  108. Los registros 10–11 (Ld↓) están documentados en V20, pero no se ha
  podido confirmar que existan con ese significado en el data model 102. La
  verificación de `info` sirve justo para eso.
- Unidad de `IO_PRESSURE`: el manual dice hPa en §15.2 p. 44 y 0,1 hPa en §15.6
  p. 49. La heurística de `sensor.pressure_to_hpa` no valida su significado;
  en el firmware antiguo, los registros 16–17 pueden representar otra cosa.
  Tampoco debe interpretarse humedad sin confirmar el mapa.
- El PMU485 está pensado como herramienta de instalación y servicio, no para
  medir de forma permanente [2]. Además, el manual pide que el conversor RS-485
  tenga aislamiento galvánico, porque las fuentes de los portátiles generan
  picos (§7.3.5). Para una instalación fija, use una fuente de 12 V y un
  conversor aislado, o el Smart Powered Hub (0382440).
- El programa de ejemplo en C del manual (§15.11) omite los `break` del
  `switch`. Tal como está escrito, siempre daría 0.0; no lo copie literalmente.

## Fuentes

1. Kipp & Zonen, *New accessory: PMU485 Smart Setup Hub* y *Instruction Manual PMU485* — <https://www.kippzonen.com/Download/1061/Manual-PMU485-Smart-Setup-Hub>
2. Equinox Instruments, ficha PMU485 (12 V DC, 50 mA + 10 mA/sensor, hasta 10 radiómetros, herramienta de instalación/servicio) — <https://www.equinoxinstruments.co.uk/products/accessories/pmu485>
3. Kintech Engineering, *Kipp & Zonen SMP10, output RS485* (asignación de hilos) — <https://www.kintech-engineering.com/pdf_docs/EN_Pyranometer_Kipp_AND_Zonen_RS485_output.pdf>
4. Kipp & Zonen, *Pyranometer interface options* V1.0 (2021): "register, which is updated once per second" — <https://www.kippzonen.com/Download/1084/Interface-options-for-Kipp-Zonen-Pyranometers>
5. Kipp & Zonen, *Instruction Manual SMP series* (edición anterior, vía Campbell Scientific) — <https://s.campbellsci.com/documents/ca/manuals/smp-series_man.pdf>
6. IEC 61724-1:2021, tabla de intervalos de muestreo y registro (resumen) — <https://www.linkedin.com/pulse/part-1-2-weather-data-acquisition-according-iec-61724-1-thomas-boldt>
