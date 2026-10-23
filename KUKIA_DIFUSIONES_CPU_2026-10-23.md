# Difusiones fuera de los nodos CPU (23/10/2026)

Cambio hecho: **el Core ya no le entrega `difusion_calor` a un nodo CPU**, ni como
tarea propia ni como réplica de verificación. Medido antes y después con el mismo
banco, la misma máquina y seis minutos por lado.

---

## Lo que estaba pasando

Una difusión cuesta **0,8 s en GPU y 19,5 s en CPU** (medido hoy, no estimado).
El planificador no distinguía y se la mandaba igual a la CPU, que gastaba el turno
entero en ella y cobraba lo mismo que una GPU.

En el ensayo de 6 minutos con un nodo CPU solo, antes del cambio:

- **15 de 50 tareas fueron difusiones** (30 % del trabajo).
- 15 × 19,5 s ≈ **292 s de los 360 s**: el **81 % del tiempo del nodo** en la tarea
  que peor hace.
- Ritmo: **8 tareas/minuto**. Parada ordenada: 21,1 s (tenía difusiones en vuelo).

---

## Lo que se ha cambiado

`coordinator/kukia_core.py`:

1. **El nodo declara su dispositivo** (`device`: `cpu`, `cuda:0`…). Se guarda en la
   tabla `nodos`. Los nodos dados de alta antes de este cambio se reconocen por el
   patrón con el que el propio worker se declara CPU: VRAM 0 y 0 TOPS (`_nodo_es_cpu`).
2. **Asignación**: si el nodo es CPU, el `SELECT` de `/tareas/pendiente` excluye
   `tipo='difusion_calor'`. Matriz e inferencia siguen abiertas para ella.
3. **Réplicas**: lo replicable son justo las tareas caras, o sea las difusiones. Un
   nodo CPU tampoco las reclama (`_reclamar_replica(..., sin_difusion=True)`).
4. **Generador**: si no hay ninguna GPU activa (visto en los últimos 5 min) no se
   fabrican difusiones, y las difusiones ya encoladas **no cuentan** para el objetivo
   de cola. Además, aunque haya GPU, no se acumulan más de media cola de difusiones.
   Sin esto el cambio tenía un fallo lógico: la cola se llenaría de difusiones que
   nadie puede servir y la CPU se quedaría sin matriz que hacer.

`worker/kukia_worker.py`: manda `device` en el registro. Un Core antiguo ignora el
campo de más, así que un worker nuevo contra un Core viejo sigue funcionando.

---

## Medición antes / después

Mismo montaje: `tools/ensayo_estabilidad.py`, Core aislado con copia verificada del
ledger, **un nodo CPU solo** (`--gpus "" --cpu 4`), 6 minutos.

| | Antes | Después |
|---|---|---|
| Difusiones entregadas al nodo CPU | **15** | **0** |
| Duración media de una difusión en CPU | 19,5 s (máx 23,7 s) | — |
| Tareas terminadas por el nodo | **50** | **1.836** |
| Matriz / inferencia | 26 / 9 | 1.480 / 356 |
| Ritmo | 8,3/min | **306/min** |
| KIA cobrados | 0,516 | 1,123 |
| CPU ocupada | 89,2 % | 81,4 % |
| Fallidas / atascadas | 0 / 0 | 0 / 0 |
| Parada ordenada | 21,1 s | **0,6 s** |

**36 veces más tareas** y ningún trabajo para el que el nodo no sirve. El KIA sube
menos que las tareas porque el pago es por GFLOP y la matriz es barata: el nodo CPU
cobra poco, pero cobra por lo que de verdad hace.

Ficheros: `medidas/ensayos/cpu_dif_antes_20261023_143116/` y
`.../cpu_dif_despues_20261023_144230/`. Se resumen con
`python tools/medir_difusiones_cpu.py <carpeta>`.

### No se ha roto lo que funcionaba

Con el Core parcheado y un nodo **GPU** (3 min): **148 difusiones**, 0,4 s de media,
máx 1,0 s. La GPU sigue recibiendo exactamente lo que le toca.

`medidas/ensayos/gpu_sigue_dif_20261023_145154/`.

---

## Lo que hay que saber

- **El cambio no está en caliente**: el Core de producción sigue con el código
  anterior porque el proceso se cargó antes de tocar el fichero. Entra en vigor al
  reiniciarlo. Ahora mismo producción tiene **cero nodos CPU** (solo `cuda:0` y
  `cuda:1`), así que reiniciar no cambiaría nada visible; el día que entre un nodo
  CPU, es lo que evita que se coma difusiones.
- Un nodo CPU ya no recibe difusiones **aunque esté solo en la red**: esas tareas
  esperan a que aparezca una GPU. Es la consecuencia buscada, y por eso el generador
  deja de fabricarlas cuando no hay GPU y no deja que ocupen la cola.
- El tope de difusiones encoladas se mira una vez por pasada del generador, así que
  se puede pasar de 60 lo que quepa en esa pasada (40). Inocuo.
- Producción no se ha tocado: ni Core, ni panel, ni minero.

---

## Pifia mía, contada entera

Al hacer una prueba de humo ("¿arranca el Core parcheado?") me equivoqué de variable
de entorno: puse `KUKIA_LEDGER_DIR`, y la buena es **`KUKIA_DB`**. Resultado: la
instancia de prueba abrió el ledger de **producción** durante ~1 segundo, no una
carpeta temporal.

Qué hizo exactamente:
- Los `ALTER TABLE` de arranque: añadió la columna `nodos.device` (la añadiría igual
  el Core nuevo en su primer arranque).
- El saneado de difusiones encoladas (recorta `pasos` al tope actual).
- Nada más: no pagó, no creó tareas (`KUKIA_AUTO_TAREAS=0`), no tocó el ledger.

Qué se comprobó después:
- `PRAGMA quick_check` sobre el ledger: **ok**.
- Auditoría completa de sellos contra el historial entero: **OK: historial intacto
  y firmado** (384.668 asientos, 227 sellos).
- Producción sirviendo: Core 200, panel 200, tareas subiendo.

Ojo con una cosa que parece un fallo y no lo es: `verificar_sellos.py --core` avisa
`ALTERADO: sello 0 ... no encadena con el anterior`. Es porque en ese modo pide solo
los últimos 200 sellos y el primero de la ventana no tiene predecesor dentro de ella.
Con el historial completo (`--db` + `--sellos`) sale OK.
