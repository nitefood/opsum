"""Tkinter GUI for OPSUM Core Firmware."""
import queue
import threading
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
import time
import struct
import os
import logging
from typing import Optional, Tuple, List

try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception:
    serial = None
    list_ports = None


class SerialWorker:
    """Background serial reader that pushes raw byte chunks into a queue."""

    def __init__(self, port, baud=115200, out_queue=None):
        self.port = port
        self.baud = baud
        self._serial = None
        self._thread = None
        self._stop = threading.Event()
        self.queue = out_queue or queue.Queue()

    @staticmethod
    def enumerate_ports():
        if list_ports is None:
            return []
        return [p.device for p in list_ports.comports()]

    def start(self):
        if serial is None:
            raise RuntimeError('pyserial not available')
        if self._thread and self._thread.is_alive():
            return
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=1)
        except Exception:
            self._serial = None
            raise
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        while not self._stop.is_set():
            iteration_start = time.monotonic()
            try:
                if not self._serial or not self._serial.is_open:
                    time.sleep(0.1)
                    continue
                n = int(getattr(self._serial, 'in_waiting', 0) or 0)
                if n <= 0:
                    time.sleep(0.01)
                    continue
                raw = self._serial.read(n)
                if not raw:
                    continue
                self.queue.put(raw)
            except Exception:
                time.sleep(0.1)
            try:
                elapsed = time.monotonic() - iteration_start
                rem = 0.01 - elapsed
                if rem > 0:
                    time.sleep(rem)
            except Exception:
                pass

    def send(self, text: str):
        if not self._serial or not self._serial.is_open:
            return False
        try:
            if not text.endswith('\n'):
                text = text + '\n'
            self._serial.write(text.encode('utf-8'))
            return True
        except Exception:
            return False

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            if self._serial and self._serial.is_open:
                self._serial.close()
        except Exception:
            pass

    @property
    def is_open(self):
        return bool(self._serial and self._serial.is_open)

# Set to True to enable serial/frame debug logging to serial_debug.log.
DEBUGLOG = False

# Logger for serial/frame debugging.
_LOG_PATH = os.path.join(os.path.dirname(__file__), 'serial_debug.log') if '__file__' in globals() else 'serial_debug.log'
_serial_logger = logging.getLogger('opsum.serial_debug')
if DEBUGLOG:
    if not _serial_logger.handlers:
        _serial_logger.setLevel(logging.DEBUG)
        try:
            fh = logging.FileHandler(_LOG_PATH, mode='a', encoding='utf-8')
            fmt = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
            fh.setFormatter(fmt)
            _serial_logger.addHandler(fh)
        except Exception:
            _serial_logger.addHandler(logging.NullHandler())
else:
    _serial_logger.addHandler(logging.NullHandler())

# Binary frame format sent by firmware in main.py: <H B B I f f f f B H>.
START_MARKER = 0x55AA
EVENT_TYPE = 0x73
END_MARKER = 0x66BB
FRAME_FORMAT = '<HBBIffffBH'
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)

REFRESH_OPTIONS = [10, 20, 50, 100, 200, 500, 1000]

THEMES = {
    'Dark': {'bg': '#000000', 'fg': '#ffffff'},
    'Dark Blue': {'bg': '#001219', 'fg': '#A8DBFF'},
    'Solarized Dark': {'bg': '#002b36', 'fg': '#839496'},
    'Light': {'bg': '#f8f8f8', 'fg': '#111111'},
}

COMMON_FONTS = ['Helvetica', 'Arial', 'Consolas', 'Times New Roman', 'Courier New']
COMMON_SIZES = [24, 32, 48, 56, 64, 72, 84]


class Tooltip:
    """Simple tooltip that accepts a string or a callable returning a string."""
    def __init__(self, widget, text_or_callable, delay=400):
        self.widget = widget
        self.text = text_or_callable
        self.delay = delay
        self.tipwindow = None
        self.id = None
        widget.bind('<Enter>', self.enter)
        widget.bind('<Leave>', self.leave)
        widget.bind('<ButtonPress>', self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hidetip()

    def schedule(self):
        self.unschedule()
        try:
            self.id = self.widget.after(self.delay, self.showtip)
        except Exception:
            self.id = None

    def unschedule(self):
        if self.id:
            try:
                self.widget.after_cancel(self.id)
            except Exception:
                pass
            self.id = None

    def showtip(self):
        if self.tipwindow:
            return
        try:
            text = self.text() if callable(self.text) else self.text or ''
        except Exception:
            text = ''
        if not text:
            return
        try:
            tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.attributes('-topmost', True)
            label = tk.Label(tw, text=text, justify='left', background='#ffffe0', relief='solid', borderwidth=1, font=(None, 9))
            label.pack(ipadx=4, ipady=2)
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + 10
            tw.wm_geometry(f"+{x}+{y}")
            self.tipwindow = tw
        except Exception:
            self.tipwindow = None

    def hidetip(self):
        if self.tipwindow:
            try:
                self.tipwindow.destroy()
            except Exception:
                pass
            self.tipwindow = None



class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title('OPSUM GUI')

        self.style = ttk.Style()
        try:
            self.style.theme_use('clam')
        except Exception:
            pass

        # state
        self.queue = queue.Queue()
        self.worker = None
        # cache for last numeric sample so we can reformat when precision changes
        self._last_voltage = None
        self._last_current = None
        self._last_power = None
        # GUI state attributes (initialized for type checkers)
        self.top_frame: Optional[tk.Frame] = None
        self._drag_window: Optional[tk.Toplevel] = None
        self._drag_action: Optional[str] = None
        self._drag_sides: Tuple[bool, bool, bool, bool] = (False, False, False, False)
        self._drag_start_geom: Optional[Tuple[int, int, int, int]] = None
        self._drag_started: bool = False
        self._pressed: bool = False
        self._saved_geometry: Optional[str] = None
        self._saved_menubar: Optional[tk.Menu] = None
        self._ports: List[str] = []

        # fonts
        self.font_family = tk.StringVar(value='Helvetica')
        self.font_size = tk.IntVar(value=72)

        # theme
        self.current_theme = 'Dark Blue'
        # disable manual resizing — window size controlled programmatically
        self._allow_manual_resize = False
        try:
            self.root.resizable(False, False)
        except Exception:
            try:
                self.root.resizable(0, 0)
            except Exception:
                pass

        # Menu
        self.menubar = tk.Menu(root)
        settings = tk.Menu(self.menubar, tearoff=0)
        theme_menu = tk.Menu(settings, tearoff=0)
        for t in THEMES:
            theme_menu.add_command(label=t, command=lambda n=t: self.apply_theme(n))
        settings.add_cascade(label='Theme', menu=theme_menu)
        font_menu = tk.Menu(settings, tearoff=0)
        font_menu.add_command(label='Increase Size', command=self.increase_font)
        font_menu.add_command(label='Decrease Size', command=self.decrease_font)
        settings.add_cascade(label='Font', menu=font_menu)
        self.menubar.add_cascade(label='Settings', menu=settings)

        # View menu (minimal UI)
        view_menu = tk.Menu(self.menubar, tearoff=0)
        self.minimal_var = tk.BooleanVar(value=False)
        view_menu.add_checkbutton(label='Minimal UI', variable=self.minimal_var, command=self.toggle_minimal)
        self.menubar.add_cascade(label='View', menu=view_menu)

        root.config(menu=self.menubar)
        # keyboard shortcuts
        root.bind('<Control-m>', lambda e: self.toggle_minimal())
        root.bind('<Escape>', lambda e: self._exit_minimal_if_active())
        # Ctrl+scroll to change font size (cross-platform bindings)
        try:
            root.bind_all('<Control-MouseWheel>', self._on_ctrl_mousewheel)
            root.bind_all('<Control-Button-4>', self._on_ctrl_mousewheel)
            root.bind_all('<Control-Button-5>', self._on_ctrl_mousewheel)
        except Exception:
            pass
        # context menu for right-click
        self.ctx_menu = tk.Menu(root, tearoff=0)
        root.bind('<Button-3>', self._on_right_click)

        # Variables for options (no top bar; options available via menus and context menu)
        self.port_var = tk.StringVar(value='')
        self.rate_var = tk.StringVar(value=str(50))
        # initialize poll interval from the chosen refresh rate
        try:
            self._poll_interval = int(self.rate_var.get())
        except Exception:
            self._poll_interval = 50
        # Decimal precision for displayed values (2,3,4,5,6)
        self.precision_var = tk.IntVar(value=3)
        # cached integer precision for fast formatting
        self._precision = int(self.precision_var.get())
        try:
            fams = tkfont.families() or []
        except Exception:
            fams = []
        families = COMMON_FONTS + sorted(set(fams) - set(COMMON_FONTS))
        self._font_families = families

        # Build option menus (Port / Refresh / Font family / Font size)
        self._menus = []
        options = tk.Menu(self.menubar, tearoff=0)
        # Ports submenu (populated dynamically)
        self.port_menu = tk.Menu(options, tearoff=0)
        options.add_cascade(label='Serial port', menu=self.port_menu)
        # Refresh submenu
        self.rate_menu = tk.Menu(options, tearoff=0)
        for r in REFRESH_OPTIONS:
            # use radiobuttons tied to rate_var
            self.rate_menu.add_radiobutton(label=str(r), variable=self.rate_var, value=str(r), command=self._on_rate_select)
        options.add_cascade(label='Refresh rate (ms)', menu=self.rate_menu)
        # Decimal precision submenu
        self.precision_menu = tk.Menu(options, tearoff=0)
        for p in (2, 3, 4, 5, 6):
            try:
                self.precision_menu.add_radiobutton(label=str(p), variable=self.precision_var, value=p, command=self._on_precision_select)
            except Exception:
                pass
        options.add_cascade(label='Decimal precision (digits)', menu=self.precision_menu)
        # Font family submenu
        self.font_family_menu = tk.Menu(options, tearoff=0)
        for fam in self._font_families:
            self.font_family_menu.add_command(label=fam, command=lambda f=fam: self._set_font_family(f))
        options.add_cascade(label='Font family', menu=self.font_family_menu)
        # Font size submenu
        self.font_size_menu = tk.Menu(options, tearoff=0)
        for s in COMMON_SIZES:
            self.font_size_menu.add_radiobutton(label=str(s), variable=self.font_size, value=s, command=lambda: self.apply_font())
        options.add_cascade(label='Font size', menu=self.font_size_menu)
        # Refresh ports command
        options.add_separator()
        options.add_command(label='Refresh Ports', command=self.populate_ports)
        self.menubar.add_cascade(label='Options', menu=options)
        # Do not explicitly theme menus here so the 'Options' menu
        # keeps the native look consistent with 'Settings' and 'View'.
        # Keep `_menus` empty to avoid applying custom menu styles.

        # drag/resize state (used in minimal mode)
        self._drag_action = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_start_geom = None

        
        # Center display
        self.disp_frame = tk.Frame(root)
        self.disp_frame.pack(expand=True, fill='both')

        self.small_font = tkfont.Font(family=self.font_family.get(), size=18, weight='bold')
        self.big_font = tkfont.Font(family=self.font_family.get(), size=self.font_size.get(), weight='bold')

        # use StringVars for fast, low-overhead text updates
        self.v_var = tk.StringVar(value='--')
        self.a_var = tk.StringVar(value='--')
        self.w_var = tk.StringVar(value='--')

        self.v_label = tk.Label(self.disp_frame, text='V', font=self.small_font)
        self.v_label.pack()
        self.v_value = tk.Label(self.disp_frame, textvariable=self.v_var, font=self.big_font)
        self.v_value.pack()

        self.a_label = tk.Label(self.disp_frame, text='A', font=self.small_font)
        self.a_label.pack(pady=(20,0))
        self.a_value = tk.Label(self.disp_frame, textvariable=self.a_var, font=self.big_font)
        self.a_value.pack()

        self.w_label = tk.Label(self.disp_frame, text='W', font=self.small_font)
        self.w_label.pack(pady=(20,0))
        self.w_value = tk.Label(self.disp_frame, textvariable=self.w_var, font=self.big_font)
        self.w_value.pack()

        # Status frame with connection LED
        self.status_frame = tk.Frame(root)
        self.status_frame.pack(fill='x', side='bottom', pady=6)
        self.status = tk.Label(self.status_frame, text='Disconnected')
        self.status.pack(side='left', padx=8)
        # LED indicator (click to toggle connect/disconnect)
        self.led_canvas = tk.Canvas(self.status_frame, width=20, height=20, highlightthickness=0)
        self.led_item = self.led_canvas.create_oval(2, 2, 18, 18, fill='red', outline='')
        self.led_canvas.pack(side='right', padx=8)
        self.led_canvas.bind('<Button-1>', lambda e: self.toggle_connect())
        # tooltip shows connect/disconnect behavior dynamically
        self.led_tooltip = Tooltip(self.led_canvas, lambda: ('Disconnect serial port\nStops polling and closes the port' if (self.worker and getattr(self.worker, 'is_open', False)) else 'Connect serial port\nOpens the selected port and starts polling'))

        # Keep a list of widgets to update theme
        self.color_widgets = [root, self.disp_frame, self.v_label, self.v_value, self.a_label, self.a_value, self.w_label, self.w_value, self.status, self.status_frame, self.led_canvas]

        # Populate initial state
        self.populate_ports()
        self.apply_theme(self.current_theme)
        self.apply_font()
        # ensure a sensible minimum window size so controls remain visible
        try:
            self._ensure_min_window_size()
            # also perform an initial snug resize so the window is usable on first run
            try:
                self._resize_to_snug()
            except Exception:
                pass
        except Exception:
            pass

        self.poll_job = None
        self._drain_job = None
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        # runtime receive buffer for fragmented serial data
        self._rx_buffer = bytearray()

    def _on_right_click(self, event):
        # build context menu depending on current mode
        try:
            self.ctx_menu.delete(0, 'end')
        except Exception:
            pass
        # Minimal UI toggle
        try:
            if getattr(self, 'minimal_var', None) and self.minimal_var.get():
                self.ctx_menu.add_command(label='Normal UI', command=lambda: self._set_minimal(False))
            else:
                self.ctx_menu.add_command(label='Minimal UI', command=lambda: self._set_minimal(True))
        except Exception:
            pass
        # Serial port submenu
        try:
            pm = tk.Menu(self.ctx_menu, tearoff=0)
            for p in getattr(self, '_ports', []):
                pm.add_radiobutton(label=p, variable=self.port_var, value=p, command=lambda p=p: self._set_port(p))
            self.ctx_menu.add_cascade(label='Serial port', menu=pm)
        except Exception:
            pass
        # Refresh rate submenu
        try:
            rm = tk.Menu(self.ctx_menu, tearoff=0)
            for r in REFRESH_OPTIONS:
                rm.add_radiobutton(label=str(r), variable=self.rate_var, value=str(r), command=self._on_rate_select)
            self.ctx_menu.add_cascade(label='Refresh rate (ms)', menu=rm)
        except Exception:
            pass
        # Decimal precision submenu (available in both normal and minimal UI)
        try:
            prm = tk.Menu(self.ctx_menu, tearoff=0)
            for p in (2, 3, 4, 5, 6):
                try:
                    prm.add_radiobutton(label=str(p), variable=self.precision_var, value=p, command=self._on_precision_select)
                except Exception:
                    pass
            self.ctx_menu.add_cascade(label='Decimal precision (digits)', menu=prm)
        except Exception:
            pass
        # Theme submenu
        try:
            tm = tk.Menu(self.ctx_menu, tearoff=0)
            for t in THEMES:
                tm.add_command(label=t, command=lambda n=t: self.apply_theme(n))
            self.ctx_menu.add_cascade(label='Theme', menu=tm)
        except Exception:
            pass
        # Font family and size
        try:
            ffm = tk.Menu(self.ctx_menu, tearoff=0)
            for fam in getattr(self, '_font_families', COMMON_FONTS):
                ffm.add_command(label=fam, command=lambda f=fam: self._set_font_family(f))
            self.ctx_menu.add_cascade(label='Font family', menu=ffm)
            fsm = tk.Menu(self.ctx_menu, tearoff=0)
            for s in COMMON_SIZES:
                fsm.add_radiobutton(label=str(s), variable=self.font_size, value=s, command=lambda: self.apply_font())
            self.ctx_menu.add_cascade(label='Font size', menu=fsm)
        except Exception:
            pass
        # Refresh ports + quit
        try:
            self.ctx_menu.add_separator()
            self.ctx_menu.add_command(label='Refresh Ports', command=self.populate_ports)
            self.ctx_menu.add_separator()
            self.ctx_menu.add_command(label='Quit', command=self.on_close)
        except Exception:
            pass
        try:
            self.ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.ctx_menu.grab_release()
            except Exception:
                pass

    

    # (anchor visual indicators removed — we use cursor-only feedback)

    def _set_minimal(self, enable: bool):
        # update variable and apply
        try:
            self.minimal_var.set(bool(enable))
        except Exception:
            self.minimal_var = tk.BooleanVar(value=bool(enable))
        self.apply_minimal(bool(enable))

    def _on_minimal_button1(self, event):
        # decide whether this is a move or a resize based on cursor position
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            mx = event.x
            my = event.y
            margin = 12
            left = mx <= margin
            right = mx >= (w - margin)
            top = my <= margin
            bottom = my >= (h - margin)
            geom = self.root.geometry()
            # parse geometry WxH+X+Y
            try:
                size, xpart, ypart = geom.split('+')
                sw, sh = size.split('x')
                start_w = int(sw)
                start_h = int(sh)
                start_x = int(xpart)
                start_y = int(ypart)
            except Exception:
                start_w = self.root.winfo_width()
                start_h = self.root.winfo_height()
                start_x = self.root.winfo_x()
                start_y = self.root.winfo_y()
            self._drag_start_geom = (start_w, start_h, start_x, start_y)
            self._drag_start_x = event.x_root
            self._drag_start_y = event.y_root
            # prepare drag state; actual overlay will be created on first motion
            self._drag_started = False
            self._pressed = True
            if left or right or top or bottom:
                if getattr(self, '_allow_manual_resize', False):
                    self._drag_action = 'resize'
                    self._drag_sides = (left, right, top, bottom)
                else:
                    # manual resize disabled — treat edge drags as moves
                    self._drag_action = 'move'
                    self._drag_sides = (False, False, False, False)
            else:
                self._drag_action = 'move'
                self._drag_sides = (False, False, False, False)
        except Exception:
            self._drag_action = None

    def _on_minimal_motion(self, event):
        if not self._drag_action:
            return
        try:
            if self._drag_start_geom is None:
                return
            start_w, start_h, start_x, start_y = self._drag_start_geom
            dx = event.x_root - self._drag_start_x
            dy = event.y_root - self._drag_start_y
            # start lightweight drag only after significant motion
            if not getattr(self, '_drag_started', False):
                if abs(dx) < 8 and abs(dy) < 8:
                    return
                try:
                    self._drag_window = tk.Toplevel()
                    dw = self._drag_window
                    if dw is None:
                        return
                    dw.overrideredirect(True)
                    try:
                        bg = THEMES.get(self.current_theme, {}).get('bg', self.root['bg'])
                    except Exception:
                        bg = self.root['bg']
                    try:
                        dw.configure(bg=bg)
                    except Exception:
                        pass
                    try:
                        dw.attributes('-alpha', 0.22)
                    except Exception:
                        pass
                    try:
                        # prefer mirroring the main window geometry, fallback to computed geometry
                        try:
                            dw.geometry(self.root.geometry())
                        except Exception:
                            dw.geometry(f"{start_w}x{start_h}+{start_x}+{start_y}")
                    except Exception:
                        pass
                    try:
                        dw.lift()
                        dw.attributes('-topmost', True)
                    except Exception:
                        pass
                    try:
                        dw.bind('<B1-Motion>', self._on_minimal_motion)
                        dw.bind('<ButtonRelease-1>', self._on_minimal_release)
                    except Exception:
                        pass
                    try:
                        # ensure we get the release even if it happens outside the overlay
                        self.root.bind_all('<ButtonRelease-1>', self._on_minimal_release)
                    except Exception:
                        pass
                    try:
                        # make main window visually transparent but keep it mapped
                        self.root.attributes('-alpha', 0.0)
                    except Exception:
                        pass
                    self._drag_started = True
                except Exception:
                    self._drag_window = None

            if self._drag_action == 'move':
                new_x = start_x + dx
                new_y = start_y + dy
                try:
                    dw = getattr(self, '_drag_window', None)
                    if dw is not None:
                        dw.geometry(f"{start_w}x{start_h}+{new_x}+{new_y}")
                    else:
                        self.root.geometry(f"{start_w}x{start_h}+{new_x}+{new_y}")
                except Exception:
                    pass
            elif self._drag_action == 'resize' and getattr(self, '_allow_manual_resize', False):
                left, right, top, bottom = self._drag_sides
                new_w = start_w
                new_h = start_h
                new_x = start_x
                new_y = start_y
                if right:
                    new_w = max(100, int(start_w + dx))
                if bottom:
                    new_h = max(60, int(start_h + dy))
                if left:
                    new_w = max(100, int(start_w - dx))
                    new_x = start_x + int(dx)
                if top:
                    new_h = max(60, int(start_h - dy))
                    new_y = start_y + int(dy)
                try:
                    dw = getattr(self, '_drag_window', None)
                    if dw is not None:
                        dw.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")
                    else:
                        self.root.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")
                except Exception:
                    pass
            # update cursor/anchors live while moving
            try:
                self._update_resize_cursor(event)
            except Exception:
                pass
        except Exception:
            pass

    def _on_minimal_release(self, event):
        # finalize drag: if we used a drag window, apply its geometry to real window
        try:
                dw = getattr(self, '_drag_window', None)
                if dw is not None:
                    geom = None
                    try:
                        # read geometry using winfo to avoid string parsing issues
                        dx = dw.winfo_x()
                        dy = dw.winfo_y()
                        dw_w = dw.winfo_width()
                        dw_h = dw.winfo_height()
                        geom = f"{dw_w}x{dw_h}+{dx}+{dy}"
                    except Exception:
                        try:
                            geom = dw.geometry()
                        except Exception:
                            geom = None
                    try:
                        # destroy drag window
                        dw.destroy()
                    except Exception:
                        pass
                    # visually hide main window content by making it transparent
                    try:
                        self.root.attributes('-alpha', 0.0)
                    except Exception:
                        pass
                    try:
                        # restore main window visual state
                        try:
                            self.root.attributes('-alpha', 1.0)
                        except Exception:
                            pass
                        try:
                            self.root.lift()
                        except Exception:
                            pass
                        try:
                            # ensure minimal override state
                            self.root.overrideredirect(True)
                        except Exception:
                            pass
                    except Exception:
                        pass
                    if geom:
                        try:
                            self.root.geometry(geom)
                        except Exception:
                            pass
                    try:
                        self._drag_window = None
                    except Exception:
                        self._drag_window = None
        except Exception:
            pass
        # cleanup pressed/drag state
        try:
            self._pressed = False
            self._drag_started = False
        except Exception:
            pass
        self._drag_action = None
        self._drag_sides = (False, False, False, False)
        self._drag_start_geom = None

    def _on_minimal_hover(self, event):
        # update cursor when hovering in minimal mode
        try:
            self._update_resize_cursor(event)
        except Exception:
            pass

    def _update_resize_cursor(self, event):
        # change cursor according to proximity to edges/corners in minimal mode
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            # compute coordinates relative to the main window
            try:
                mx = event.x_root - self.root.winfo_rootx()
                my = event.y_root - self.root.winfo_rooty()
            except Exception:
                mx = getattr(event, 'x', 0)
                my = getattr(event, 'y', 0)
            # Do not show resize cursors when manual resizing is disabled
            if not getattr(self, '_allow_manual_resize', False):
                cur = 'arrow'
            else:
                margin = 12
                left = mx <= margin
                right = mx >= (w - margin)
                top = my <= margin
                bottom = my >= (h - margin)
                if (left and top) or (right and bottom):
                    cur = 'size_nw_se'
                elif (right and top) or (left and bottom):
                    cur = 'size_ne_sw'
                elif left or right:
                    cur = 'size_we'
                elif top or bottom:
                    cur = 'size_ns'
                else:
                    cur = 'arrow'
            try:
                self.root.configure(cursor=cur)
            except Exception:
                try:
                    self.root.config(cursor=cur)
                except Exception:
                    pass
        except Exception:
            pass

    def populate_ports(self):
        try:
            ports = SerialWorker.enumerate_ports()
        except Exception:
            ports = []
        # keep an internal list and populate the port menu
        self._ports = ports
        try:
            self.port_menu.delete(0, 'end')
            for p in ports:
                self.port_menu.add_radiobutton(label=p, variable=self.port_var, value=p, command=lambda p=p: self._set_port(p))
        except Exception:
            pass
        if ports:
            self.port_var.set(ports[0])
        else:
            self.port_var.set('')

    def apply_theme(self, name: str):
        self.current_theme = name
        t = THEMES.get(name, THEMES['Dark'])
        bg = t['bg']
        fg = t['fg']
        # update basic widgets
        try:
            self.root.configure(bg=bg)
        except Exception:
            pass
        for w in self.color_widgets:
            try:
                w.configure(bg=bg)
            except Exception:
                try:
                    w.configure(background=bg)
                except Exception:
                    pass
            try:
                w.configure(fg=fg)
            except Exception:
                try:
                    w.configure(foreground=fg)
                except Exception:
                    pass
        # ttk style tweaks
        style = self.style
        style.configure('TButton', background=bg, foreground=fg)
        style.configure('TLabel', background=bg, foreground=fg)
        # Custom combobox style for better contrast and padding
        try:
            style.configure('Custom.TCombobox', fieldbackground=bg, background=bg, foreground=fg, padding=6)
            style.map('Custom.TCombobox', fieldbackground=[('readonly', bg)], foreground=[('readonly', fg)])
        except Exception:
            try:
                style.configure('TCombobox', fieldbackground=bg, background=bg, foreground=fg)
            except Exception:
                pass
        self.status.configure(fg=fg)
        # also try to theme known menus/widgets
        for m in getattr(self, '_menus', []):
            try:
                m.configure(background=bg, foreground=fg, activebackground=fg, activeforeground=bg)
            except Exception:
                try:
                    m.configure(bg=bg, fg=fg)
                except Exception:
                    pass
        try:
            # update LED and status visuals
            self._update_led()
        except Exception:
            pass

    # Helper methods for menu-driven options
    def _set_port(self, port: str):
        try:
            self.port_var.set(port)
        except Exception:
            pass
        try:
            self.status.configure(text=f'Port selected: {port}')
        except Exception:
            pass

    def _on_rate_select(self):
        try:
            # ensure poll interval picks up new value
            self._poll_interval = int(self.rate_var.get())
        except Exception:
            self._poll_interval = 200
        try:
            self.status.configure(text=f'Refresh rate: {self.rate_var.get()} ms')
        except Exception:
            pass

    def _on_precision_select(self):
        try:
            self._precision = int(self.precision_var.get())
        except Exception:
            self._precision = 4
        try:
            self.status.configure(text=f'Decimal precision: {self._precision} digits')
        except Exception:
            pass
        # Reformat currently displayed values to the newly selected precision
        try:
            fmt = f'{{:.{self._precision}f}}'
            if getattr(self, '_last_voltage', None) is not None:
                try:
                    self.v_var.set(fmt.format(self._last_voltage))
                except Exception:
                    pass
                try:
                    self.a_var.set(fmt.format(self._last_current))
                except Exception:
                    pass
                try:
                    self.w_var.set(fmt.format(self._last_power))
                except Exception:
                    pass
            else:
                # fallback: try parsing current displayed strings and reformat
                try:
                    v = float(self.v_var.get())
                    a = float(self.a_var.get())
                    w = float(self.w_var.get())
                    try:
                        self.v_var.set(fmt.format(v))
                    except Exception:
                        pass
                    try:
                        self.a_var.set(fmt.format(a))
                    except Exception:
                        pass
                    try:
                        self.w_var.set(fmt.format(w))
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                self._resize_to_snug()
            except Exception:
                pass
        except Exception:
            pass

    def _set_font_family(self, fam: str):
        try:
            self.font_family.set(fam)
        except Exception:
            pass
        try:
            self.apply_font()
        except Exception:
            pass

    def _update_led(self):
        try:
            connected = bool(self.worker and getattr(self.worker, 'is_open', False))
        except Exception:
            connected = False
        color = '#00c853' if connected else '#c62828'
        try:
            self.led_canvas.itemconfigure(self.led_item, fill=color)
        except Exception:
            pass

    def apply_font(self):
        fam = self.font_family.get()
        size = int(self.font_size.get())
        self.small_font.config(family=fam, size=max(12, int(size/4)), weight='bold')
        self.big_font.config(family=fam, size=size, weight='bold')
        # reassign fonts
        for lbl in (self.v_label, self.v_value, self.a_label, self.a_value, self.w_label, self.w_value):
            if lbl in (self.v_value, self.a_value, self.w_value):
                lbl.configure(font=self.big_font)
            else:
                lbl.configure(font=self.small_font)
        try:
            self._ensure_min_window_size()
        except Exception:
            pass
        # Ensure the window resizes to fit the new font immediately
        try:
            self._resize_to_snug()
        except Exception:
            pass

    def increase_font(self):
        self.font_size.set(self.font_size.get() + 8)
        self.apply_font()

    def decrease_font(self):
        self.font_size.set(max(12, self.font_size.get() - 8))
        self.apply_font()

    def _ensure_min_window_size(self):
        """Ensure the main window has a sensible minimum size and initial geometry.
        Skip enforcing min size when in minimal mode."""
        try:
            if getattr(self, 'minimal_var', None) and self.minimal_var.get():
                return
        except Exception:
            pass
        try:
            # let layouts settle
            self.root.update_idletasks()
            # compute required/requested size and enforce sensible minima
            try:
                req_w = self.root.winfo_reqwidth()
                req_h = self.root.winfo_reqheight()
            except Exception:
                req_w = 420
                req_h = 240
            min_w = max(420, req_w + 20)
            min_h = max(240, req_h + 20)
            try:
                self.root.minsize(min_w, min_h)
            except Exception:
                try:
                    self.root.wm_minsize(min_w, min_h)
                except Exception:
                    pass
            try:
                cur_w = self.root.winfo_width()
                cur_h = self.root.winfo_height()
                if cur_w < min_w or cur_h < min_h:
                    # set an initial geometry large enough to show controls
                    try:
                        self.root.geometry(f"{min_w}x{min_h}")
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

    def _on_ctrl_mousewheel(self, event):
        """Handle Ctrl+scroll: change font size by +/-5 per notch."""
        try:
            # Windows / macOS: event.delta exists (positive = up)
            if hasattr(event, 'delta'):
                d = event.delta
                sign = 1 if d > 0 else -1 if d < 0 else 0
            else:
                # X11: event.num == 4 (up) or 5 (down)
                if getattr(event, 'num', None) == 4:
                    sign = 1
                elif getattr(event, 'num', None) == 5:
                    sign = -1
                else:
                    sign = 0
            if sign:
                # use 5 points per scroll tick for snappy changes
                self._change_font_by(sign * 5)
        except Exception:
            pass

    def _change_font_by(self, delta: int):
        """Adjust `self.font_size` by `delta` (±1), apply font and resize window."""
        try:
            cur = int(self.font_size.get())
            new = max(8, min(300, cur + int(delta)))
            if new == cur:
                return
            self.font_size.set(new)
            # apply_font updates fonts synchronously
            self.apply_font()
            # resize to snugly fit the new text size
            try:
                self._resize_to_snug()
            except Exception:
                pass
        except Exception:
            pass

    def _resize_to_snug(self):
        """Resize the window to fit current content while keeping sane limits."""
        try:
            self.root.update_idletasks()

            # In minimal mode, keep current position and only tighten dimensions.
            if getattr(self, 'minimal_var', None) and self.minimal_var.get():
                if getattr(self, '_pressed', False) or getattr(self, '_drag_started', False):
                    return
                try:
                    val_w = max(self.v_value.winfo_reqwidth(), self.a_value.winfo_reqwidth(), self.w_value.winfo_reqwidth())
                except Exception:
                    val_w = self.disp_frame.winfo_reqwidth()
                try:
                    total_h = self.disp_frame.winfo_reqheight() + 8
                except Exception:
                    total_h = 120
                new_w = max(80, val_w + 12)
                new_h = max(48, total_h)
                try:
                    x = self.root.winfo_x()
                    y = self.root.winfo_y()
                except Exception:
                    x = 0
                    y = 0
                try:
                    self.root.geometry(f"{new_w}x{new_h}+{x}+{y}")
                except Exception:
                    pass
                return

            # Normal mode sizing.
            req_w = self.root.winfo_reqwidth()
            req_h = self.root.winfo_reqheight()
            new_w = max(420, req_w + 24)
            new_h = max(240, req_h + 24)
            try:
                self.root.minsize(max(420, req_w + 20), max(240, req_h + 20))
            except Exception:
                pass
            try:
                x = self.root.winfo_x()
                y = self.root.winfo_y()
            except Exception:
                x = 0
                y = 0
            try:
                self.root.geometry(f"{new_w}x{new_h}+{x}+{y}")
            except Exception:
                pass
        except Exception:
            pass

    def toggle_minimal(self):
        # Called from menu or shortcut; toggle according to variable
        try:
            enable = bool(self.minimal_var.get())
        except Exception:
            enable = False
        self.apply_minimal(enable)

    def apply_minimal(self, enable: bool):
        if enable:
            try:
                self._saved_menubar = self.root.cget('menu')
            except Exception:
                self._saved_menubar = None
            try:
                self.root.config(menu='')
            except Exception:
                pass
            try:
                self._saved_geometry = self.root.geometry()
            except Exception:
                self._saved_geometry = None

            try:
                self.root.overrideredirect(True)
                self.root.update_idletasks()
                try:
                    self.root.minsize(1, 1)
                except Exception:
                    pass
                try:
                    val_w = max(self.v_value.winfo_reqwidth(), self.a_value.winfo_reqwidth(), self.w_value.winfo_reqwidth())
                except Exception:
                    val_w = self.disp_frame.winfo_reqwidth()
                try:
                    total_h = self.disp_frame.winfo_reqheight() + 8
                except Exception:
                    total_h = 120
                new_w = max(80, val_w + 12)
                new_h = max(48, total_h)
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                x = max(0, (sw - new_w) // 2)
                y = max(0, (sh - new_h) // 2)
                self.root.geometry(f"{new_w}x{new_h}+{x}+{y}")
                self.root.deiconify()
                self.root.lift()
                self.root.attributes('-topmost', True)
                self.root.bind_all('<Button-1>', self._on_minimal_button1)
                self.root.bind_all('<B1-Motion>', self._on_minimal_motion)
                self.root.bind_all('<ButtonRelease-1>', self._on_minimal_release)
                self.root.bind_all('<Motion>', self._on_minimal_hover)
            except Exception:
                pass
        else:
            try:
                self.root.overrideredirect(False)
            except Exception:
                pass
            try:
                if getattr(self, '_saved_menubar', None):
                    self.root.config(menu=self._saved_menubar)
                else:
                    self.root.config(menu=self.menubar)
            except Exception:
                pass
            try:
                if self.top_frame is not None:
                    self.top_frame.pack(padx=8, pady=8, anchor='n', before=self.disp_frame)
            except Exception:
                pass
            try:
                if self.status_frame is not None:
                    self.status_frame.pack(fill='x', side='bottom', pady=6, after=self.disp_frame)
            except Exception:
                pass
            try:
                if getattr(self, '_saved_geometry', None):
                    self.root.geometry(self._saved_geometry)
                self.root.attributes('-topmost', False)
            except Exception:
                pass
            try:
                self.root.unbind_all('<Button-1>')
                self.root.unbind_all('<B1-Motion>')
                self.root.unbind_all('<ButtonRelease-1>')
                self.root.unbind_all('<Motion>')
            except Exception:
                pass
            try:
                self.root.configure(cursor='')
            except Exception:
                pass
            try:
                self._ensure_min_window_size()
            except Exception:
                pass
            try:
                self._resize_to_snug()
            except Exception:
                pass

    def _exit_minimal_if_active(self):
        if getattr(self, 'minimal_var', None) and self.minimal_var.get():
            try:
                self.minimal_var.set(False)
            except Exception:
                self.minimal_var = tk.BooleanVar(value=False)
            self.apply_minimal(False)
        else:
            self.on_close()

    def toggle_connect(self):
        if self.worker and self.worker.is_open:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_var.get()
        if not port:
            self.status.configure(text='No port selected')
            return
        try:
            self.worker = SerialWorker(port, 115200, out_queue=self.queue)
            self.worker.start()
        except Exception as e:
            self.status.configure(text=f'Open failed: {e}')
            self.worker = None
            return
        try:
            self.status.configure(text=f'Connected: {port}')
        except Exception:
            pass
        try:
            self._update_led()
        except Exception:
            pass
        self.start_poll()

    def disconnect(self):
        if self.worker:
            try:
                self.worker.close()
            except Exception:
                pass
        self.worker = None
        try:
            self.status.configure(text='Disconnected')
        except Exception:
            pass
        try:
            self._update_led()
        except Exception:
            pass
        self.stop_poll()

    def start_poll(self):
        self.stop_poll()
        try:
            self._poll_interval = int(self.rate_var.get())
        except Exception:
            self._poll_interval = 200
        # start fast serial drain (10ms) and UI update loop (user-selected)
        try:
            self._serial_drain_loop()
        except Exception:
            pass
        try:
            self._poll_loop()
        except Exception:
            pass

    def stop_poll(self):
        if self.poll_job:
            try:
                self.root.after_cancel(self.poll_job)
            except Exception:
                pass
            self.poll_job = None
        if getattr(self, '_drain_job', None):
            try:
                self.root.after_cancel(self._drain_job)
            except Exception:
                pass
            self._drain_job = None

    def _poll_loop(self):
        # UI update loop: update displayed values from cached `_last_` values
        try:
            self._apply_last_values()
        except Exception:
            pass
        self.poll_job = self.root.after(self._poll_interval, self._poll_loop)

    def _serial_drain_loop(self):
        """Fast serial drain running at ~10ms intervals.

        This always consumes queued chunks and feeds them into the parser
        so reads happen independently of the GUI refresh rate.
        """
        try:
            while True:
                try:
                    chunk = self.queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    # parse chunk into internal cache (do not update UI here)
                    self._handle_raw_chunk(chunk)
                except Exception:
                    pass
        except Exception:
            pass
        # schedule next drain
        try:
            self._drain_job = self.root.after(10, self._serial_drain_loop)
        except Exception:
            self._drain_job = None

    def _apply_last_values(self):
        """Apply cached numeric values to the UI (runs at user-selected rate)."""
        try:
            precision = int(getattr(self, '_precision', self.precision_var.get()))
        except Exception:
            precision = 4
        fmt = f'{{:.{precision}f}}'
        try:
            if getattr(self, '_last_voltage', None) is not None:
                try:
                    self.v_var.set(fmt.format(self._last_voltage))
                except Exception:
                    pass
            if getattr(self, '_last_current', None) is not None:
                try:
                    self.a_var.set(fmt.format(self._last_current))
                except Exception:
                    pass
            if getattr(self, '_last_power', None) is not None:
                try:
                    self.w_var.set(fmt.format(self._last_power))
                except Exception:
                    pass
            # update status with last timestamp/seq if available
            try:
                ts = getattr(self, '_last_timestamp', None)
                seq = getattr(self, '_last_seq', None)
                if ts is not None:
                    if seq is not None:
                        self.status.configure(text=f'OK t={ts} seq={seq}')
                    else:
                        self.status.configure(text=f'OK t={ts}')
            except Exception:
                pass
        except Exception:
            pass

    def _handle_raw_chunk(self, raw_chunk):
        """Append raw bytes from the serial worker and parse fixed binary frames."""
        if raw_chunk is None:
            return

        if isinstance(raw_chunk, (bytes, bytearray, memoryview)):
            data = bytes(raw_chunk)
        else:
            self._log_malformed_frame(str(raw_chunk).encode('utf-8', errors='replace'), 'non-bytes chunk')
            return

        if not data:
            return

        self._rx_buffer.extend(data)

        self._process_rx_buffer()

    def _process_rx_buffer(self):
        """Extract and validate complete fixed-size frames from `self._rx_buffer`."""
        start_bytes = struct.pack('<H', START_MARKER)
        while True:
            idx = self._rx_buffer.find(start_bytes)
            if idx == -1:
                # Keep at most the last byte so split markers can be recovered.
                if len(self._rx_buffer) > 1:
                    del self._rx_buffer[:-1]
                break

            if idx > 0:
                noise = bytes(self._rx_buffer[:idx])
                self._log_malformed_frame(noise, 'drop leading noise before start marker')
                del self._rx_buffer[:idx]

            if len(self._rx_buffer) < FRAME_SIZE:
                break

            frame = bytes(self._rx_buffer[:FRAME_SIZE])
            if self._consume_frame_bytes(frame):
                del self._rx_buffer[:FRAME_SIZE]
                continue
            self._log_malformed_frame(frame, 'invalid frame at start marker; resync by 1 byte')
            del self._rx_buffer[0]

    def _consume_frame_bytes(self, frame: bytes) -> bool:
        """Validate and decode one binary frame. Return True on success."""
        if len(frame) != FRAME_SIZE:
            return False

        try:
            start, ev, seq, timestamp, voltage, current, shunt, power, checksum, end = struct.unpack(FRAME_FORMAT, frame)
        except struct.error:
            return False

        if start != START_MARKER or ev != EVENT_TYPE or end != END_MARKER:
            return False

        payload = frame[2:-3]  # event..power
        expected = 0
        for b in payload:
            expected ^= b
        if expected != checksum:
            return False

        # Cache numeric values; UI refreshes from this cache.
        self._last_voltage = float(voltage)
        self._last_current = float(current)
        self._last_power = float(power)
        self._last_timestamp = int(timestamp)
        self._last_seq = int(seq)
        return True

    def _log_malformed_frame(self, raw: bytes, reason: str):
        """Write a detailed log entry for malformed binary frames."""
        if not DEBUGLOG:
            return

        if raw is None:
            payload_repr = '<None>'
            hex_dump = ''
            payload_len = 0
        else:
            b = bytes(raw)
            payload_repr = repr(b)
            payload_len = len(b)
            # show up to first 256 bytes in hex for readability
            hex_dump = ' '.join(f'{x:02x}' for x in b[:256])

        msg = f"Malformed frame: reason={reason}; len={payload_len}; hex={hex_dump}; repr={payload_repr}"
        try:
            _serial_logger.error(msg)
        except Exception:
            # Last-resort fallback if logging handler is unavailable.
            with open(_LOG_PATH, 'a', encoding='utf-8', errors='replace') as f:
                from datetime import datetime
                f.write(f"{datetime.utcnow().isoformat()}Z ERROR: {msg}\n")

    def on_close(self):
        try:
            if self.worker:
                self.worker.close()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
