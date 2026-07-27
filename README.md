# Mini Monitor de Recursos Linux

Aplicación Python con UI dark profesional para monitorear recursos en Linux.

## Ejecución en Arch / Garuda

```bash
sudo pacman -Syu
sudo pacman -S python tk git sqlite procps-ng iproute2 coreutils util-linux
cd mini_monitor_linux
python app.py
```

Verificar Tkinter:

```bash
python -m tkinter
```

## Módulos incluidos

- CPU: núcleos, frecuencia, porcentaje de utilización y load average.
- Memoria: total, usada, disponible, caché, buffers y swap.
- Procesos: PID, nombre, estado, usuario, CPU y memoria.
- Usuarios: usando `who -u`.
- Disco: total, usado, libre y tasas de lectura/escritura.
- Red: interfaces, IPs, gateway y tráfico.
- Histórico: gráficas de CPU, CPU por núcleo, memoria, red, disco, swap y load average.
- CRUD SQLite: crear, consultar, actualizar y eliminar capturas.

## Requisitos técnicos evidenciados

- `/proc/cpuinfo`, `/proc/stat`, `/proc/meminfo`, `/proc/net/dev`, `/proc/loadavg`, `/proc/diskstats`.
- `threading.Thread()` con dos hilos concurrentes.
- `os.fork()` con evidencia en `fork_child.log`.
- `subprocess` para ejecutar `ps`, `who`, `df`, `free`, `ip`.
- `os.system()` con evidencia en `os_system_demo.log`.
- Almacenamiento local SQLite en `monitor.db`.
