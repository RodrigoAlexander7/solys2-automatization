# Tiempo y reloj

## De dónde sale la hora de cada muestra

La pone el PC al terminar de leer cada sensor, en UTC con milisegundos. Las
lecturas de un ciclo son **secuenciales** (Modbus RTU tiene un solo maestro y
una línea): entre el primer y el último sensor pasan decenas de milisegundos.
El número de ciclo agrupa lecturas, pero no indica simultaneidad.

## Por qué importa el reloj

- La posición del sol depende de la hora. Cerca del amanecer el sol sube unos
  0,25° por minuto: con 30 s de error, `cos z` puede errar varios por ciento y
  con él los límites BSRN y la DNI estimada.
- Para comparar con otros equipos (otro observatorio, un ceilómetro) los relojes
  deben coincidir.

Los relojes de Windows pueden desviarse varios segundos si la sincronización
automática no está activa o es poco frecuente.

El manual de operaciones de BSRN pide relojes dentro de **1 s**: el criterio
original era ±1 % del período de promedio (0,6 s para 1 minuto) y se relajó a
un segundo ([BSRN Operations Manual V2.1 §2.3.1, pp. 13–14](/manuales/BSRN-standars/BSRN_Operations_Manual_V21__WCRP21_TD1274_BSRN.pdf#page=25)).
El mismo manual sugiere comprobar la hora con la simetría de la curva de un día
despejado alrededor del mediodía solar (§9.3.1.4).

## Qué hace el programa

- Cada 10 minutos (configurable), la adquisición consulta un servidor NTP
  (`pool.ntp.org` por defecto) desde un hilo aparte, sin retrasar las lecturas.
- Registra el **desfase** (reloj del servidor − reloj del PC), el retardo de la
  consulta y el servidor como evento `clock_check`, o `clock_unavailable` si no
  hubo respuesta.
- **No ajusta el reloj ni corrige marcas de tiempo.** Las comprobaciones viajan
  en `metadata.json` y `eventos.csv` de cada exportación, para corregir después
  si hace falta.

El monitor muestra un aviso si el desfase supera 1 s (el criterio de BSRN;
configurable), si no hay referencia NTP o si la última comprobación es antigua.

## Si aparece un aviso de reloj

1. Verifique que Windows sincronice la hora automáticamente (Configuración →
   Hora e idioma) o con un servidor NTP del laboratorio.
2. Si el PC no tiene internet, configure `SMP_NTP_SERVER` con un servidor local
   o deje la comprobación vacía; el aviso indicará que está deshabilitada.
3. Anote en la bitácora el momento del ajuste: las muestras anteriores conservan
   la hora con la que se registraron.

## Hora local

La interfaz muestra hora local del navegador; los archivos guardan UTC y una
columna con la hora local del PC que exportó.
