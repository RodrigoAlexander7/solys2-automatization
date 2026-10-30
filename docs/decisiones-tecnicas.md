# Decisiones técnicas

Cada decisión indica el documento y la sección exacta que la justifica.

Este archivo explica el **código actual**. Para aprender las conversiones use
primero la [guía de registros](lectura-registros.md), con tablas explicativas y
páginas verificadas. La arquitectura SQLite/FastAPI/SvelteKit está implementada;
su operación está en [operacion-dashboard.md](operacion-dashboard.md) y su
trazabilidad en [SDD](sdd/tasks.md).

**Documentos citados**

| Clave | Documento |
|---|---|
| **[MAN]** | `references/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf` (OTT HydroMet, 0374214, 02-0326) |
| **[DS]** | `references/pyranometer-SMP22x/KippZonen_Data_sheet_SMPx_2025_online.pdf`, tabla "Technical Specifications" |
| **[SGR]** | `references/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf` (manual de operación CGR4/SGR4 V10) |
| **[SGRDS]** | `references/pyrgeometer-SGR4/110101MEKZWTdsPyrgeometersEN.pdf` (folleto de pirgeómetros Kipp & Zonen) |
| **[MAN2015]** | Kipp & Zonen, *Instruction Manual SMP series*, 1 ene 2015 (vía Campbell Scientific): <https://s.campbellsci.com/documents/ca/manuals/smp-series_man.pdf> |
| **[IFACE]** | Kipp & Zonen, *Pyranometer interface options* V1.0, 24 jun 2021: <https://www.kippzonen.com/Download/1084/Interface-options-for-Kipp-Zonen-Pyranometers> |
| **[PMU]** | Ficha del PMU485, Equinox Instruments: <https://www.equinoxinstruments.co.uk/products/accessories/pmu485>; manual PMU485: <https://www.kippzonen.com/Download/1061/Manual-PMU485-Smart-Setup-Hub> |
| **[KIN]** | Kintech, *Kipp & Zonen SMP10 output RS485*: <https://www.kintech-engineering.com/pdf_docs/EN_Pyranometer_Kipp_AND_Zonen_RS485_output.pdf> |
| **[IEC]** | IEC 61724-1:2021, requisitos de muestreo y registro (resumen): <https://www.linkedin.com/pulse/part-1-2-weather-data-acquisition-according-iec-61724-1-thomas-boldt> |

---

## D1. Protocolo: Modbus RTU sobre RS-485 de 2 hilos

- **Decisión:** usar Modbus RTU sobre RS-485 de 2 hilos. El código usa solo la función 0x04 (leer input registers) y la 0x05 (escribir un coil).
- **Fuente:**
  - [MAN] §5.1: "2-wire smart interface with RS-485 Modbus® (RTU) protocol".
  - [MAN] §15.1, tabla de funciones: 0x01–0x06 y 0x10.
- **Código:** `registers.py` (`FC_READ_INPUT_REGISTERS`, `FC_WRITE_SINGLE_COIL`).

## D2. Parámetros serie: 19200 baud, 8 bits, paridad par, 1 bit de parada

- **Decisión:** usar por defecto los parámetros de fábrica. Se pueden cambiar con `--baud` y `--parity`.
- **Fuente:**
  - [MAN] §8.1 "Instrument set-up", lista de parámetros de comunicación de fábrica.
  - [MAN] §11.1: todas las direcciones deben ser únicas.
- **Código:** `registers.py` (`DEFAULT_*`), `cli.py` (`_add_bus_args`).

## D3. Conversor PMU485 y cableado

- **Decisión:** el PMU485 hace de conversor USB–RS-485 (chip FTDI) y de fuente de 12 V. El programa detecta el puerto buscando el identificador de fabricante (VID) de FTDI, 0x0403.
- **Fuente:**
  - [MAN] §2.2: el PMU485 (0382460) figura como accesorio.
  - [MAN] §7.3.5, figura "Connection to RS-485 converter": amarillo y gris = Modbus RS-485, azul = común, blanco = alimentación de 5 a 30 V DC, negro = tierra.
  - [PMU]: 12 V DC; 50 mA más 10 mA por cada sensor adicional; hasta 10 radiómetros en paralelo; pensado como "installation or service tool".
- **Polaridad de los hilos de datos:** [MAN] no dice cuál es + y cuál es −. [KIN] (tabla de pines) indica amarillo = Data+ y gris = Data−.
- **Aislamiento:** [MAN] §7.3.5, NOTICE: el conversor debe tener aislamiento galvánico.
- **Terminación:** [MAN] §7.3.5 recomienda una terminación de 120–150 Ω en el último dispositivo del bus.
- **Código:** `transport.py` (`autodetect_port`, `SerialBus`).

## D4. Mapa de registros leídos

Referencia detallada: [guía de registros, tabla periódica](lectura-registros.md#4-tabla-de-lectura-periódica).
Las direcciones y tipos se verificaron en [MAN] §15.2 pp. 43–45; las
conversiones, en §15.6 pp. 47–49. La página impresa coincide con la del PDF.
Son direcciones PDU base cero, distintas de las direcciones de equipo 2, 3 y 4.

Cada ciclo hace una sola transacción FC04 por sensor: 8 registros (2 a 9) en los piranómetros y 10 registros (2 a 11) en el pirgeómetro.

| Reg. | Nombre | Conversión | Fuente [MAN] |
|---|---|---|---|
| 2 | IO_OPERATIONAL_MODE | 1 = normal … 5 = error | §15.2, §15.6 |
| 3 | IO_STATUS_FLAGS | bits | §15.6 (tabla de bits) |
| 4 | IO_SCALE_FACTOR | S16 | §15.6 |
| 5 | IO_SENSOR1_DATA | S16 ÷ escala → W/m². En el SGR4, radiación neta | §15.2, §15.6 |
| 6 | IO_RAW_SENSOR1_DATA | S16 ÷ escala | §15.6 |
| 7 | IO_STDEV_SENSOR1 | ÷10 → W/m² | §15.6 |
| 8 | IO_BODY_TEMPERATURE | ÷10 → °C | §15.6 |
| 9 | IO_EXT_POWER_SENSOR | ÷10 → V | §15.6 |
| 10 | IO_SENSOR2_DATA | S16 ÷ escala → W/m². Solo SGR: Ld↓ compensado | §15.2, §15.6 (registro 4) |
| 11 | IO_RAW_SENSOR2_DATA | S16 ÷ escala → W/m². Solo SGR: Ld↓ | §15.2 |

Cada 60 s se hace además una lectura de los registros 16 y 17 (IO_RH e IO_PRESSURE), según [MAN] §15.2 p. 44 y §15.6 p. 49. Su interpretación no está validada para los equipos antiguos (D12).

- **Por qué el bloque 2–9:** es el mismo bloque que lee el programa de ejemplo en C ([MAN] §15.11, `SendModbusRequest (0x04, 2, IO_OPERATIONAL_MODE, 4)`), ampliado hasta el registro 9. Así basta una transacción por sensor.
- **Por qué no los registros flotantes 10000+:** existen ([MAN] §15.2, "Real time floating data points"), pero el manual no indica el orden de las palabras de 16 bits dentro de cada valor F32. Además, [MAN2015] Apéndice A no los incluye, así que un sensor con firmware antiguo podría no tenerlos. Ver D12.
- **Identificación del sensor:** se leen los registros 0–1 y 41–45 ([MAN] §15.2, §15.10). El número de serie único es lote + número de serie ([MAN] §15.10).
- **Código:** `registers.py`, `sensor.py`.

## D5. Aplicación del factor de escala

- **Decisión:** aplicar el factor con una tabla: 2 → ÷100, 1 → ÷10, 0 → ×1, −1 → ×10. Un valor que no esté en la tabla se marca como error.
- **Fuente:** [MAN] §15.6 p. 48, registro 4, tabla "Scale factor / Calculation". Se interpreta primero el signo S16; la escala solo afecta a 5, 6, 10 y 11. Las escalas de 7, 8 y 9 son fijas.
- **Nota:** el `switch` de [MAN] §15.11 no tiene ningún `break`. Tal como está escrito, siempre acaba en `default: fSensorData = 0.0`, por eso no se copió.
- **Código:** `sensor.scale_sensor_value`.

## D6. Unidad de IO_PRESSURE

- **Decisión:** si el valor bruto es mayor que 2000, se interpreta en 0,1 hPa. Si no, en hPa.
- **Fuente de la ambigüedad:** [MAN] §15.2 p. 44 dice hPa; §15.6 p. 49 (registro 17) dice 0,1 hPa y da ÷10. El umbral es una heurística del proyecto y no garantiza compatibilidad. La fila de humedad de p. 49 también tiene una unidad inconsistente con p. 44. Ver guía, sección 5.
- **Código:** `sensor.pressure_to_hpa`.

## D7. Criterio de validez de una muestra

- **Decisión:** una muestra es válida si el sensor responde, está en modo 1 (normal) y no tiene activo ninguno de los bits 0 (VOID), 1 (OVERFLOW), 2 (UNDERFLOW), 4 (ADC), 6 (CALIBRATION) ni 12 (BODY_TEMP).
- **Fuente:**
  - [MAN] §15.6: tabla de bits de IO_STATUS_FLAGS y modos de IO_OPERATIONAL_MODE ("When the error mode (5) is set, then there is a fatal error").
  - [MAN] §15.8: IO_VOID_DATA_FLAG ("data … is not valid") y OVERFLOW/UNDERFLOW.
- **Por qué los bits 3 (ERROR) y 8 (POWER_FAILED) no invalidan la muestra:** según [MAN] §15.8 (IO_ERROR_FLAG), "The error flag is always set after a power up … The computer should raise the IO_CLEAR_ERROR". Es decir, se activan en cada arranque y no indican un dato malo.
- **Por qué el bit 11 (desecante) no invalida la muestra:** es un aviso de mantenimiento ([MAN] §15.6). Además, [MAN] §10.1 indica cambiar el desecante cada 10 años.
- **Código:** `registers.INVALID_DATA_MASK`, `Sample.valid`.

## D8. Borrado de errores con el coil 10 (IO_CLEAR_ERROR)

- **Decisión:** al iniciar, y como mucho una vez cada 30 s si reaparecen los bits 3, 4, 5 u 8, escribir `true` en el coil 10.
- **Fuente:**
  - [MAN] §15.9, coil 10: "Setting this coil will clear the error only when the error is a non-fatal error".
  - [MAN] §15.11: el programa de ejemplo lo escribe cuando `uStatusFlags != 0`.
  - [MAN] §15.5, nota "i": los valores por defecto se restauran al encender. Escribir el coil no cambia la configuración guardada.
- **Código:** `SMPSensor.clear_error`, `Acquisition._clear_error`.

## D9. Frecuencia de muestreo: 1 s, con promedios de 1 min

- **Decisión:** leer cada 1 s y guardar también promedios de 1 minuto. El programa rechaza un intervalo más corto que el tiempo que tarda un ciclo, avisa si el intervalo es menor de 1 s y avisa si es mayor de 3 s.

| Argumento | Fuente exacta |
|---|---|
| "An optimal setting for the data interval is to sample every second and store one minute averages" | [MAN] §9.2 "Collecting data" |
| El registro de irradiancia "is updated once per second" | [IFACE], p. 1 |
| Por defecto, el software del fabricante lee una vez por segundo ("1 poll per second is the default setting") | [MAN2015] §4.1 "Set up communication", paso 4 |
| Tiempo de respuesta: 63 % en menos de 0,7 s y 95 % en menos de 2 s | [DS] "Response time (63 %)" y "(95 %)"; [MAN] §14.1 |
| El programa de ejemplo espera 1 s entre lecturas (`Delay (1000)`) | [MAN] §15.11 |
| IEC 61724-1 Clase A pide muestrear cada 3 s o menos y registrar cada 1 min o menos | [IEC] |

- **Efecto sobre IO_STDEV_SENSOR1:** su valor depende de cada cuánto se lee. [MAN] §15.6, registro 7: "If the poll frequency is quite high (for example 1 poll per second) then the standard deviation will be zero or almost zero". Por eso la variabilidad real se calcula en `minute_avg.csv` (columna `std_wm2`).
- **Código:** `cli.check_interval`, `acquisition._MinuteAggregator`.

## D10. Presupuesto de tiempo del bus

- **Tiempo de un carácter:** 11 bits (1 de inicio + 8 de datos + 1 de paridad + 1 de parada) a 19200 baud = 0,573 ms ([MAN] §8.1). Con paridad `N` son 10 bits, porque el programa abre el puerto siempre con 1 bit de parada.
- **Formato de las tramas FC04:**
  - Petición de 8 bytes = 4,6 ms.
  - Respuesta de 5 + 2N bytes; con N = 8 son 21 bytes = 12,0 ms; con N = 10 (pirgeómetro) son 25 bytes = 14,3 ms.
  - Hay además dos silencios t3.5 ("Modbus over serial line V1.02", documento que cita [MAN] §15.1).
  - Total en el cable: **20,6 ms** por piranómetro y **22,9 ms** por pirgeómetro. Lo comprueba `tests/test_smp22x.py::test_wire_time_19200`.
- **En `run.json`:** el tiempo teórico se calcula con los baudios y la paridad con que se abrió el bus (`bus.baudrate`, `bus.parity`), no con los de fábrica.
- **Medición en la prueba 01:**
  - Tiempo de ida y vuelta (RTT) medio: 28, 32 y 34 ms.
  - Ciclo completo de 3 sensores: 93 ms de media (p95: 109 ms).
  - Ocupación del bus: **~9 %** leyendo cada 1 s.
  - Ciclos perdidos: 0.
- **Consecuencia:** leer cada 1 s deja más de un 90 % del tiempo libre. Un timeout de 0,3 s en un sensor sigue cabiendo dentro del ciclo.
- **Código:** `transport.read_transaction_wire_time_s`.

## D11. Un solo maestro y peticiones en serie

- **Decisión:** un único proceso lee los tres sensores, uno detrás de otro, sin hilos concurrentes. Un sensor que falla 5 veces seguidas solo se reintenta cada 10 ciclos.
- **Fuente:**
  - [MAN] §7.3.5, figura "Connection to a RS-485 network": un maestro y varios esclavos.
  - [MAN2015] §4 (introducción): el software "is not intended for continuous long-term measurement".
- **Consecuencia:** no se debe abrir SmartExplorer a la vez que el programa. Usarían el mismo puerto COM y habría dos maestros en el bus.

## D12. Compatibilidad con el firmware detectado en la prueba 01

| Sensor | IO_DEVICE_TYPE | Data model | Software |
|---|---|---|---|
| #2 | 611 | 102 | 300 |
| #3 | 607 | 100 | 201 |
| #4 | 607 | 100 | 201 |

- **Qué dicen los manuales:**
  - [MAN] §15.6 (registro 0) solo documenta los códigos 635 (SMP10x) y 636 (SMP22x). En el registro 1 exige un data model ≥ 108.
  - [MAN2015] Apéndice A (registros 0 y 1) es válido para los data models 100 y 101. Solo lista los códigos 601 (CMP 3) y 603 (CMP 11).
  - Ninguno de los dos documentos define los códigos 607 ni 611.
- **Evidencia de compatibilidad parcial:** las tablas citadas describen el bloque 2–9 y sus escalas. Eso respalda su interpretación, pero no demuestra que solo cambie el nombre del modelo ni valida todos los registros del firmware real. `valid=1` es una decisión del software, no una certificación de compatibilidad.
- **Decisión:** mantener el aviso, pero no bloquear la adquisición. **No se usan los registros 10000+ ni los 16–17 como fuente de datos principal**, porque [MAN2015] Apéndice A asigna los registros 16–17 a IO_DAC_OUTPUT_VOLTAGE e IO_SELECTED_DAC_INPUT. En estos sensores, por tanto, las columnas `rh_pct` y `pressure_hpa` **no deben interpretarse**.
- **Actualización:** el sensor #2 es un pirgeómetro SGR4, no un SMP22x (ver D15). Por eso el programa asocia el código 611 al SGR4 y el 607 al SMP22x (`registers.OBSERVED_DEVICE_TYPES`), y avisa en cada identificación de que esos códigos no están en [MAN].
- **Pendiente:** confirmar con OTT HydroMet los códigos 607 y 611. Hay que dar lote y serie: 240466, 240349 y 240350 ([MAN] §12.1).

## D13. Tiempos de estabilización

- Tras encenderse, el sensor puede tardar hasta 3 h en estabilizar su temperatura ([MAN] §7.3.5, nota "i": "It may take three hours…").
- Tras un reinicio por cambio de configuración, tarda unos 1 min en volver a operar ([MAN] §8.1.3 y §8.1.4).

## D14. Formato de almacenamiento

- **Decisión actualizada:** SQLite WAL como fuente de verdad, con commit por ciclo y escritor único. Conservar CSV largo, promedios y `run.json` como proyecciones compatibles posteriores al commit; pueden regenerarse con `export`. No son una transacción atómica conjunta con SQLite.
- **Fuente:** [MAN] §9.1: "The instrument has no internal data memory". El PC es el único registro de los datos, así que el CSV se vacía a disco en cada ciclo.
- **Datos incluidos en `run.json`:** [MAN] §12.1 pide, para cualquier soporte técnico, el modelo, el número de serie, archivos de datos de ejemplo y el sistema de adquisición.
- **Estadísticas de comunicación:** `comm_errors = requests − responses_ok` cuenta solo fallos del sensor (timeout, CRC, excepción Modbus). Una desconexión del USB va aparte, en `port_errors` y `port_downtime_s`, y no cuenta como petición. El ciclo cortado por la desconexión no entra en `cycle_ms_*` ni en la ocupación del bus, y los ciclos que no se hicieron mientras faltaba el puerto no cuentan como `overruns`.

- **Durabilidad:** `flush` vacía buffers Python; no garantiza persistencia ante corte de energía. SQLite usa `synchronous=FULL`; backup mediante la API SQLite, no copiando solo el `.db` activo. Las palabras U16 y el factor se conservan ahora en BD, sin prometer recuperarlos de CSV antiguos.

## D15. Sensor #2: pirgeómetro SGR4

- **Hecho:** la dirección 2 es un pirgeómetro Kipp & Zonen SGR4, no un piranómetro. Mide radiación infrarroja (onda larga, 4,5–42 µm) del cielo, no luz solar ([SGR] §5.1, §13.1).
- **Qué entrega por Modbus.** [SGR] no trae mapa de registros. El mapa común de los sensores smart está en [MAN] §15.2, que marca qué registros cambian en el SGR:
  - Registro 5, IO_SENSOR1_DATA: "Temperature compensated radiation in W/m2 (**Net radiation for SGR**)".
  - Registro 10, IO_SENSOR2_DATA: "Temperature compensated long wave down radiation in W/m2 (only for SGR)".
  - Registro 11, IO_RAW_SENSOR2_DATA: "Long wave down radiation in W/m2 (only for SGR)".
  - [MAN] §15.6, registro 4: el factor de escala se aplica también a IO_SENSOR2_DATA e IO_RAW_SENSOR2_DATA.
- **Física:** [SGR] §7.1.5 da Ld = Uemf/S + 5,67·10⁻⁸·Tb⁴. Uemf/S es la radiación neta entre el cielo y el detector y Tb la temperatura del cuerpo en K. La neta "is mostly negative", porque el cielo suele estar más frío que el sensor. [SGRDS] lo resume: "if the sky is colder than the pyrgeometer, … the output is negative".
- **Interpretación de la prueba 01.** El valor de −160 a −163 W/m² del sensor #2 ([prueba 01](prueba-01.md)) es compatible con radiación neta de un SGR4. Identificarlo como pirgeómetro explica por qué no debe compararse con la irradiancia de #3/#4; el valor solo no demuestra cielo despejado ni confirma todo el mapa del firmware.
- **Decisión:**
  - Configurar por dirección qué sensores son pirgeómetros (`--pyrgeometers`, por defecto `2`), en lugar de deducirlo del IO_DEVICE_TYPE, porque el código 611 no está documentado. Si el código y la configuración no cuadran, `read_info` avisa.
  - En el pirgeómetro, leer los registros 2–11 en una sola transacción y tomar **Ld↓ (registro 10) como magnitud principal**: es la que se promedia por minuto y se grafica. La neta (registro 5) se guarda también.
  - Mostrar en `info`, en `onda_larga.png` y en `resumen.csv` la verificación Ld↓ − (neta + σT⁴). Debe quedar dentro de ±5 W/m² (`sensor.LW_CHECK_TOLERANCE_WM2`), que cubre la resolución de 1 W/m² con escala 0 y la de 0,1 °C de la temperatura (≈ 0,6 W/m²).
  - Graficar el pirgeómetro aparte de los piranómetros y sacarlo de la "media de los sensores": mide otra magnitud.
- **Páginas verificadas:** [MAN] §15.2 p. 43 (registros 5 y 10), p. 44 (11); §15.6 p. 48 (escala). [SGR] §7.1.5 p. 17 (fórmula), §8.1 p. 24 (serie), §13.1 p. 29 (prestaciones).
- **Umbral y límites:** ±5 W/m² es una tolerancia elegida por el proyecto, no una especificación de calibración del fabricante. La coincidencia es consistencia interna; ambas vías usan datos del instrumento. Hoy no modifica `Sample.valid`.
- **Riesgo pendiente:** el SGR4 de la prueba 01 tiene data model 102 y [MAN] describe el 108. No se ha confirmado 10–11 con ese significado en el 102. Calcular Ld↓ desde neta y temperatura sería una alternativa que también requiere validar el perfil y etiquetar el resultado como calculado; no se activa automáticamente en el código actual.
- **Código:** `registers.py` (`IO_SENSOR2_DATA`, `PYRGEOMETER_BLOCK_COUNT`, `OBSERVED_DEVICE_TYPES`), `sensor.py` (`Sample.measurement`, `Sample.lw_down_calc`), `cli.py` (`--pyrgeometers`), `plotting.py` (`_plot_longwave`).
