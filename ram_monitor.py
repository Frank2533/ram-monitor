#!/usr/bin/env python3
"""Floating, click-through RAM monitor (X11/GNOME).

Drag by the top bar. Hold Alt and click a process to kill it (SIGTERM, like pkill).
"""
import os
import signal
from collections import defaultdict

import cairo
import gi
import psutil

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

CSS = b"""
window { background-color: transparent; }
.card {
    background-color: rgba(17, 20, 28, 0.30);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px;
    padding: 10px 14px 12px 14px;
}
label { color: rgba(236, 240, 245, 0.60); font-family: "Ubuntu", sans-serif; font-size: 10pt;
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8); }
.dim { color: rgba(236, 240, 245, 0.40); font-size: 8.5pt; }
.title { font-weight: bold; font-size: 10.5pt; }
.big { font-size: 20pt; font-weight: bold; }
.section { color: rgba(236, 240, 245, 0.40); font-size: 8pt; font-weight: bold; letter-spacing: 1px;
           margin-top: 8px; }
.mono { font-family: "Ubuntu Mono", monospace; }
#handle { border-radius: 8px; padding: 2px 4px; }
#handle:hover { background-color: rgba(255, 255, 255, 0.08); }

progressbar trough { min-height: 8px; border: none; border-radius: 4px;
                     background-color: rgba(255, 255, 255, 0.08); }
progressbar progress { min-height: 8px; border: none; border-radius: 4px; background-color: #4ade80; }
progressbar.warn progress { background-color: #fbbf24; }
progressbar.crit progress { background-color: #f87171; }
.thin trough, .thin progress { min-height: 3px; }
.thin progress { background-color: #60a5fa; }

.row { border-radius: 6px; padding: 3px 4px; }
.armed .card { border-color: rgba(248, 113, 113, 0.7); }
.armed .row label.name { color: #fca5a5; }
.armed .row:hover { background-color: rgba(248, 113, 113, 0.22); }
.armed .thin progress { background-color: #f87171; }
"""

SHOW_PROCS_AT = 85
HINT = 'Alt+click = kill'
ARMED_HINT = 'click to kill'


def human(n):
    return f'{n / 2 ** 30:.1f} GB' if n >= 2 ** 30 else f'{n / 2 ** 20:.0f} MB'


def label(text='', *classes, xalign=0.0):
    lbl = Gtk.Label(label=text, xalign=xalign)
    for c in classes:
        lbl.get_style_context().add_class(c)
    return lbl


class Monitor(Gtk.Window):
    def __init__(self):
        super().__init__()
        # DOCK: stays above everything and is not hidden by Super+D (show desktop).
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_keep_above(True)
        self.stick()
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_visual(self.get_screen().get_rgba_visual())
        self.set_app_paintable(True)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class('card')
        card.set_size_request(320, -1)

        # Header / drag handle
        self.handle = Gtk.EventBox(name='handle')
        head = Gtk.Box(spacing=8)
        head.pack_start(label('⠿  Memory', 'title'), False, False, 0)
        self.hint = label(HINT, 'dim', xalign=1.0)
        head.pack_end(self.hint, False, False, 0)
        self.handle.add(head)
        self.handle.add_events(Gdk.EventMask.BUTTON_MOTION_MASK)
        self.handle.connect('button-press-event', self.drag_start)
        self.handle.connect('motion-notify-event', self.drag_move)
        card.add(self.handle)

        # Overall usage
        summary = Gtk.Box(spacing=10)
        self.percent = label('', 'big')
        summary.pack_start(self.percent, False, False, 0)
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.used = label()
        self.free = label('', 'dim')
        detail.add(self.used)
        detail.add(self.free)
        summary.pack_start(detail, False, False, 0)
        card.add(summary)
        self.bar = Gtk.ProgressBar()
        card.add(self.bar)

        # Top processes
        # Process list only appears when RAM is above SHOW_PROCS_AT percent.
        self.proc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.proc_box.add(label('TOP PROCESSES', 'section'))
        card.add(self.proc_box)
        self.rows = []
        for _ in range(10):
            ev = Gtk.EventBox()
            ev.get_style_context().add_class('row')
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            line = Gtk.Box(spacing=6)
            name = label('', 'name')
            name.set_ellipsize(Pango.EllipsizeMode.END)
            pid = label('', 'dim', 'mono')
            size = label('', 'mono', xalign=1.0)
            line.pack_start(name, True, True, 0)
            line.pack_start(pid, False, False, 0)
            line.pack_end(size, False, False, 0)
            mini = Gtk.ProgressBar()
            mini.get_style_context().add_class('thin')
            col.add(line)
            col.add(mini)
            ev.add(col)
            row = dict(name=name, pid=pid, size=size, bar=mini, proc=None)
            ev.connect('button-press-event', self.kill, row)
            self.proc_box.add(ev)
            self.rows.append(row)
        self.add(card)

        self.shape_state = None
        geo = (self.get_display().get_primary_monitor() or self.get_display().get_monitor(0)).get_geometry()
        self.move(geo.x + geo.width - 380, geo.y + 50)
        self.show_all()
        self.refresh()  # after show_all, so it can hide the process list
        GLib.timeout_add(2000, self.refresh)
        GLib.timeout_add(50, self.poll_alt)

    def refresh(self):
        vm = psutil.virtual_memory()
        self.percent.set_text(f'{vm.percent:.0f}%')
        self.used.set_text(f'{human(vm.used)} used of {human(vm.total)}')
        self.free.set_text(f'{human(vm.available)} free')
        self.bar.set_fraction(vm.percent / 100)
        ctx = self.bar.get_style_context()
        for cls, on in (('warn', 70 <= vm.percent < 90), ('crit', vm.percent >= 90)):
            (ctx.add_class if on else ctx.remove_class)(cls)

        if vm.percent < SHOW_PROCS_AT:
            if self.proc_box.get_visible():
                self.proc_box.hide()
                self.resize(1, 1)  # shrink back to just the summary
            return True
        self.proc_box.show_all()

        # Group by app (exe name), like pkill does: one "brave" row instead of ten.
        # ponytail: summed RSS double-counts shared pages, use USS/PSS if exact numbers matter.
        apps = defaultdict(lambda: [0, []])
        for p in psutil.process_iter(['pid', 'name', 'exe', 'memory_info']):
            i = p.info
            if i['memory_info']:
                exe = os.path.basename(i['exe'] or '')
                # exe names threads like "MainThread" -> node; but versioned binaries ("2.1.301") keep their name
                app = apps[exe if exe[:1].isalpha() else i['name'] or '?']
                app[0] += i['memory_info'].rss
                app[1].append(i['pid'])
        top = sorted(apps.items(), key=lambda kv: kv[1][0], reverse=True)[:10]
        for row, (name, (rss, pids)) in zip(self.rows, top):
            row['proc'] = pids
            row['name'].set_text(name)
            row['pid'].set_text(f'×{len(pids)}' if len(pids) > 1 else str(pids[0]))
            row['size'].set_text(human(rss))
            row['bar'].set_fraction(min(rss / vm.total, 1))  # share of total RAM
        return True

    def poll_alt(self):
        # XQueryPointer reports global modifier state, so this sees Alt even while we're unfocused.
        ptr = self.get_display().get_default_seat().get_pointer()
        alt = bool(self.get_window().get_device_position(ptr)[3] & Gdk.ModifierType.MOD1_MASK)
        a = self.handle.get_allocation()
        state = (alt, a.x, a.y, a.width, a.height)
        if state != self.shape_state:
            self.shape_state = state
            ctx = self.get_style_context()
            if alt:
                ctx.add_class('armed')
                self.hint.set_text(ARMED_HINT)
                self.input_shape_combine_region(None)  # whole window clickable
            else:
                ctx.remove_class('armed')
                self.hint.set_text(HINT)
                # Only the drag bar takes input; every other click falls through to the app below.
                self.input_shape_combine_region(cairo.Region(cairo.RectangleInt(a.x, a.y, a.width, a.height)))
        return True

    def drag_start(self, _w, ev):
        x, y = self.get_position()
        self.offset = (ev.x_root - x, ev.y_root - y)

    def drag_move(self, _w, ev):
        self.move(int(ev.x_root - self.offset[0]), int(ev.y_root - self.offset[1]))

    def kill(self, _w, ev, row):
        if ev.state & Gdk.ModifierType.MOD1_MASK and row['proc']:
            name, failed = row['name'].get_text(), 0
            for pid in row['proc']:
                try:
                    psutil.Process(pid).terminate()
                except psutil.Error:
                    failed += 1
            row['name'].set_text(f'⚠ {name}: {failed} not killed' if failed else f'✕ killed {name}')


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    style = Gtk.CssProvider()
    style.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), style,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    Monitor()
    Gtk.main()
