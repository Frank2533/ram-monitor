# RAM Monitor

A small floating RAM monitor for Linux (X11 / GNOME).

- Always on top, visible on every workspace, survives **Super+D** (show desktop)
- Translucent, and **click-through**: clicks go to the app underneath
- Shows used / free / total RAM with a colour-coded bar (green < 70%, amber < 90%, red ≥ 90%)
- When RAM usage reaches **85%**, shows the top 10 memory-hungry apps (processes grouped by app name)
- **Drag** it by the top bar
- **Alt + click** an app to kill it (SIGTERM to every process in the group, like `pkill`)

## Requirements

- An X11 session (Wayland doesn't allow click-through / always-on-top for apps)
- Python 3 with GTK 3 bindings, pycairo and psutil:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-psutil
```

## Run

```bash
./ram_monitor.py
# or in the background
nohup ./ram_monitor.py >/dev/null 2>&1 &
```

## Start on login

Create `~/.config/autostart/ram-monitor.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=RAM Monitor
Exec=/usr/bin/python3 /path/to/ram_monitor.py
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
```

## Tweaks

At the top of `ram_monitor.py`:

- `SHOW_PROCS_AT` – RAM % at which the process list appears
- `CSS` – card opacity (`.card` background alpha), text opacity (`label` colour alpha), colours

## Notes

- Processes owned by root or other users can't be killed; the row says how many weren't.
- Grouped sizes add up each process's RSS, so apps with shared memory (browsers) are slightly over-counted.
