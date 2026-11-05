"""Cliente portátil de escritorio. Todas las autorizaciones se verifican en servidor."""
import json
import queue
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .importers import read_cpa

BG = '#070c0a'
PANEL = '#0e1b15'
GREEN = '#39ff88'
TEXT = '#e5fff0'
MUTED = '#84a995'
RED = '#541d2a'
YELLOW = '#544515'


class API:
    def __init__(self, url):
        self.url = url.rstrip('/')
        self.token = ''

    def call(self, path, **data):
        request = Request(self.url + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get('error', 'Error del servidor.')
            except (ValueError, AttributeError):
                detail = f'Error HTTP {exc.code}'
            raise ValueError(detail) from None
        except (URLError, TimeoutError, OSError):
            raise ValueError('Sin conexión con el servidor central. Comprueba la red y que el servidor esté abierto.') from None


class Desktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('CORTE SK2 / 2027')
        self.geometry('1300x820')
        self.minsize(1020, 680)
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background=BG, foreground=TEXT, fieldbackground=PANEL, font=('Segoe UI', 10))
        style.configure('TButton', background=PANEL, foreground=GREEN, padding=(12, 9), bordercolor=GREEN)
        style.map('TButton', background=[('active', '#16402b')])
        style.configure('TEntry', fieldbackground=PANEL, foreground=TEXT, padding=8)
        style.configure('TCombobox', fieldbackground=PANEL, foreground=TEXT, padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', TEXT)])
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=32, borderwidth=0)
        style.configure('Treeview.Heading', background='#123624', foreground=GREEN, padding=8)
        style.map('Treeview', background=[('selected', '#226341')])
        style.configure('TNotebook.Tab', padding=(18, 10))
        self.events = queue.Queue()
        self.busy = False
        self.poll_id = None
        self.data = None
        self.pending_report = None
        self.frame = ttk.Frame(self, padding=24)
        self.frame.pack(fill='both', expand=True)
        self.status = tk.StringVar(value='Conecta con el servicio central de planta')
        ttk.Label(self, textvariable=self.status, foreground=MUTED, padding=(24, 10)).pack(fill='x')
        self.after(100, self.pump)
        self.login_screen()

    def job(self, work, success, quiet=False):
        if self.busy:
            if not quiet:
                self.status.set('Espera a que termine la operación en curso.')
            return False
        self.busy = True
        def run():
            try:
                self.events.put((success, work(), None, quiet))
            except Exception as exc:
                self.events.put((success, None, str(exc), quiet))
        threading.Thread(target=run, daemon=True).start()
        return True

    def pump(self):
        try:
            while True:
                callback, result, error, quiet = self.events.get_nowait()
                self.busy = False
                if error:
                    self.status.set(error)
                    if not quiet:
                        messagebox.showerror('Corte SK2', error, parent=self)
                else:
                    callback(result)
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def clear(self):
        for widget in self.frame.winfo_children():
            widget.destroy()

    def title_block(self, parent, title, subtitle):
        ttk.Label(parent, text=title, foreground=GREEN, font=('Segoe UI', 24, 'bold')).pack(anchor='w')
        ttk.Label(parent, text=subtitle, foreground=MUTED).pack(anchor='w', pady=(4, 20))

    def login_screen(self):
        self.clear()
        box = ttk.Frame(self.frame, padding=35)
        box.place(relx=.5, rely=.45, anchor='center', width=480)
        self.title_block(box, 'CORTE SK2 / 2027', 'CONTROL DE PRODUCCIÓN  /  SAKIRA 2')
        self.url = tk.StringVar(value='http://localhost:8765')
        config = Path.home() / 'CorteSK2-cliente.json'
        try:
            self.url.set(json.loads(config.read_text(encoding='utf-8'))['server'])
        except (OSError, ValueError, KeyError):
            pass
        self.code, self.password = tk.StringVar(), tk.StringVar()
        for label, variable, secret in [('Servidor central', self.url, False), ('Código de empleado', self.code, False), ('Clave', self.password, True)]:
            ttk.Label(box, text=label).pack(anchor='w', pady=(8, 3))
            entry = ttk.Entry(box, textvariable=variable, show='•' if secret else '')
            entry.pack(fill='x')
            entry.bind('<Return>', lambda event: self.login())
        ttk.Button(box, text='ENTRAR →', command=self.login).pack(fill='x', pady=(22, 8))
        ttk.Label(box, text='Primera entrada: clave 123456. Luego debes cambiarla.', foreground=MUTED).pack(anchor='w')

    def login(self):
        url, code, password = self.url.get().strip(), self.code.get().strip(), self.password.get()
        if not url.startswith(('http://', 'https://')):
            messagebox.showerror('Servidor', 'Usa una dirección http:// o https:// completa.')
            return
        self.api = API(url)
        def success(result):
            self.api.token = result['token']
            try:
                (Path.home() / 'CorteSK2-cliente.json').write_text(json.dumps({'server': url}), encoding='utf-8')
            except OSError:
                pass
            self.password.set('')
            if result['user']['must_change']:
                self.password_screen(password)
            else:
                self.fetch_initial()
        self.job(lambda: self.api.call('/login', code=code, password=password), success)

    def password_screen(self, old):
        self.clear()
        box = ttk.Frame(self.frame, padding=40)
        box.place(relx=.5, rely=.45, anchor='center', width=480)
        self.title_block(box, 'TU NUEVA CLAVE', 'Cambio obligatorio · mínimo 8 caracteres')
        first, second = tk.StringVar(), tk.StringVar()
        for label, variable in [('Nueva clave', first), ('Repite la clave', second)]:
            ttk.Label(box, text=label).pack(anchor='w', pady=5)
            ttk.Entry(box, textvariable=variable, show='•').pack(fill='x')
        def save():
            if first.get() != second.get():
                messagebox.showerror('Clave', 'Las claves no coinciden.')
                return
            value = first.get()
            self.job(lambda: self.api.call('/password', old=old, new=value), lambda _: self.fetch_initial())
        ttk.Button(box, text='GUARDAR Y ENTRAR', command=save).pack(fill='x', pady=20)

    def fetch_initial(self):
        self.job(lambda: self.api.call('/snapshot'), self.build_dashboard)

    def build_dashboard(self, data):
        self.clear()
        self.data = data
        self.supervisor = data['user']['role'] == 'supervisor'
        top = ttk.Frame(self.frame)
        top.pack(fill='x')
        self.title_block(top, 'CORTE SK2 / 2027', f"{data['user']['name']}  ·  {'SUPERVISIÓN' if self.supervisor else 'OPERADOR'}")
        ttk.Button(top, text='Cerrar sesión', command=self.logout).place(relx=1, x=0, y=4, anchor='ne')
        self.tabs = ttk.Notebook(self.frame)
        self.tabs.pack(fill='both', expand=True)
        self.report_tab = ttk.Frame(self.tabs, padding=15)
        self.tabs.add(self.report_tab, text='PRODUCCIÓN')
        self.make_report()
        if self.supervisor:
            self.management_tab = ttk.Frame(self.tabs, padding=15)
            self.chart_tab = ttk.Frame(self.tabs, padding=15)
            self.tabs.add(self.management_tab, text='LÁSERES Y EQUIPO')
            self.tabs.add(self.chart_tab, text='RESUMEN Y GRÁFICOS')
            self.make_management()
            self.make_charts()
        self.paint(data)
        self.poll_id = self.after(5000, self.poll)

    def logout(self):
        if self.busy:
            return
        if self.pending_report and not messagebox.askyesno('Reporte pendiente', 'Hay un envío sin confirmar. ¿Cerrar sesión y descartar su reintento?'):
            return
        def done(_):
            if self.poll_id:
                self.after_cancel(self.poll_id)
                self.poll_id = None
            self.data = None
            self.pending_report = None
            self.api.token = ''
            self.login_screen()
            self.status.set('Sesión cerrada')
        self.job(lambda: self.api.call('/logout'), done)

    def make_report(self):
        self.metrics = tk.StringVar()
        ttk.Label(self.report_tab, textvariable=self.metrics, foreground=GREEN, font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(0, 15))
        self.waiting = ttk.Label(self.report_tab, text='ESPERANDO A SER ASIGNADO\nHabla con tu supervisor', foreground=GREEN, font=('Segoe UI', 24, 'bold'), justify='center')
        self.capture = ttk.Frame(self.report_tab)
        self.capture.pack(fill='x')
        self.order, self.line, self.qty, self.comment = (tk.StringVar() for _ in range(4))
        self.laser_choice = tk.StringVar()
        if self.supervisor:
            ttk.Label(self.capture, text='Láser').grid(row=0, column=0, sticky='w')
            self.report_laser = ttk.Combobox(self.capture, textvariable=self.laser_choice, state='readonly', width=18)
            self.report_laser.grid(row=1, column=0, padx=(0, 8))
        self.report_date = tk.StringVar(value=self.data['today'])
        offset = 1 if self.supervisor else 0
        for index, (label, variable, width) in enumerate([('Fecha AAAA-MM-DD', self.report_date, 13), ('Orden (u ORDEN|SET)', self.order, 21), ('Línea / Set', self.line, 12), ('Piezas', self.qty, 10), ('Comentario', self.comment, 18)], offset):
            ttk.Label(self.capture, text=label).grid(row=0, column=index, sticky='w')
            entry = ttk.Entry(self.capture, textvariable=variable, width=width)
            entry.grid(row=1, column=index, padx=(0, 8), sticky='ew')
            if variable is self.order:
                self.order_entry = entry
                entry.bind('<FocusOut>', self.parse_barcode)
            entry.bind('<Return>', lambda event: self.submit())
        self.reprint = tk.BooleanVar()
        ttk.Checkbutton(self.capture, text='Reprint', variable=self.reprint).grid(row=2, column=offset, sticky='w', pady=8)
        ttk.Button(self.capture, text='REGISTRAR / REINTENTAR →', command=self.submit).grid(row=2, column=offset + 1, columnspan=3, sticky='e', pady=8)
        tools = ttk.Frame(self.report_tab)
        tools.pack(fill='x', pady=10)
        self.filter_laser, self.only_alerts, self.only_today = tk.StringVar(value='Todas'), tk.BooleanVar(), tk.BooleanVar(value=True)
        if self.supervisor:
            self.filter_box = ttk.Combobox(tools, textvariable=self.filter_laser, state='readonly', width=22)
            self.filter_box.pack(side='left', padx=(0, 12))
            self.filter_box.bind('<<ComboboxSelected>>', lambda e: self.paint_rows())
        ttk.Checkbutton(tools, text='Solo incidencias', variable=self.only_alerts, command=self.paint_rows).pack(side='left', padx=8)
        ttk.Checkbutton(tools, text='Solo hoy', variable=self.only_today, command=self.paint_rows).pack(side='left', padx=8)
        ttk.Button(tools, text='Actualizar', command=self.refresh).pack(side='right')
        table_frame = ttk.Frame(self.report_tab)
        table_frame.pack(fill='both', expand=True)
        cols = ('report_date', 'laser', 'ord', 'line', 'qty', 'employee', 'comment', 'alert')
        self.table = ttk.Treeview(table_frame, columns=cols, show='headings', selectmode='browse')
        for col, title, width in zip(cols, ['Fecha / hora', 'Láser', 'Orden', 'Línea', 'Piezas', 'Empleado', 'Comentario', 'Validación'], [165, 120, 105, 65, 65, 95, 190, 450]):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, minwidth=50, stretch=False)
        self.table.tag_configure('red', background=RED, foreground='#ffdce4')
        self.table.tag_configure('yellow', background=YELLOW, foreground='#fff2b6')
        y = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        x = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.bind('<Double-1>', self.show_detail)

    def parse_barcode(self, event=None):
        if '|' in self.order.get():
            order, line = self.order.get().split('|', 1)
            self.order.set(order.strip())
            self.line.set(line.strip().upper())

    def submit(self):
        if self.busy:
            return
        self.parse_barcode()
        laser_id = self.laser_id(self.laser_choice.get()) if self.supervisor else None
        fields = {'order': self.order.get().strip(), 'line': self.line.get().strip(), 'qty': self.qty.get().strip(), 'comment': self.comment.get(), 'reprint': self.reprint.get(), 'laser_id': laser_id, 'report_date': self.report_date.get().strip()}
        if self.pending_report and fields != self.pending_report['fields']:
            messagebox.showwarning('Envío pendiente', 'El envío anterior no se confirmó. Restaura sus datos y reintenta antes de registrar otro. Si persiste, consulta al supervisor.')
            return
        if self.pending_report is None:
            self.pending_report = {'fields': fields, 'request_id': str(uuid.uuid4())}
        request_id = self.pending_report['request_id']
        def work():
            try:
                return self.api.call('/report', request_id=request_id, **fields)
            except ValueError as exc:
                # Los errores de validación son definitivos; los de red pueden ocultar un guardado exitoso.
                if not str(exc).startswith('Sin conexión'):
                    return {'validation_error': str(exc)}
                raise
        def done(result):
            if 'validation_error' in result:
                self.pending_report = None
                messagebox.showerror('Revisa el reporte', result['validation_error'])
                return
            self.pending_report = None
            for variable in (self.order, self.line, self.qty, self.comment):
                variable.set('')
            self.reprint.set(False)
            self.status.set(f"Registro #{result['id']} guardado en el servidor · sincronización con Sheets pendiente")
            self.order_entry.focus_set()
            self.refresh()
        self.job(work, done)

    def laser_id(self, name):
        return next((r['id'] for r in self.data['lasers'] if r['name'] == name), None)

    def make_management(self):
        toolbar = ttk.Frame(self.management_tab)
        toolbar.pack(fill='x', pady=(0, 15))
        for index, (label, command) in enumerate([('Nuevo empleado', self.add_employee), ('Nueva láser', self.add_laser), ('Editar láser / cuenta CPA', self.edit_laser), ('Quitar asignación', self.unassign), ('Activar / desactivar', self.toggle_laser), ('Cargar base CPA', self.import_cpa), ('Sincronizar Sheets', self.sync)]):
            ttk.Button(toolbar, text=label, command=command).grid(row=index // 4, column=index % 4, sticky='ew', padx=(0, 6), pady=3)
        ttk.Label(self.management_tab, text='Arrastra un empleado de la izquierda sobre su láser. También puedes seleccionarlos y pulsar ASIGNAR.', foreground=MUTED).pack(anchor='w', pady=12)
        body = ttk.Frame(self.management_tab)
        body.pack(fill='both', expand=True)
        self.employee_list = tk.Listbox(body, bg=PANEL, fg=TEXT, selectbackground='#226341', font=('Segoe UI', 12), exportselection=False, relief='flat', highlightbackground=GREEN, highlightthickness=1)
        self.laser_list = tk.Listbox(body, bg=PANEL, fg=GREEN, selectbackground='#226341', font=('Segoe UI', 13), exportselection=False, relief='flat', highlightbackground=GREEN, highlightthickness=1)
        self.employee_list.pack(side='left', fill='both', expand=True, padx=(0, 20))
        self.laser_list.pack(side='left', fill='both', expand=True)
        self.drag_code = None
        self.employee_list.bind('<ButtonPress-1>', self.drag_start)
        self.employee_list.bind('<ButtonRelease-1>', self.drag_end)
        ttk.Button(self.management_tab, text='ASIGNAR EMPLEADO A LÁSER →', command=self.assign_selected).pack(anchor='e', pady=15)

    def drag_start(self, event):
        index = self.employee_list.nearest(event.y)
        self.drag_code = self.data['employees'][index]['code'] if self.data['employees'] and index >= 0 else None

    def drag_end(self, event):
        if self.drag_code and self.winfo_containing(event.x_root, event.y_root) == self.laser_list:
            index = self.laser_list.nearest(event.y_root - self.laser_list.winfo_rooty())
            if 0 <= index < len(self.data['lasers']):
                code, laser_id = self.drag_code, self.data['lasers'][index]['id']
                self.job(lambda: self.api.call('/assign', code=code, laser_id=laser_id), lambda _: self.refresh())
        self.drag_code = None

    def selected_employee(self):
        indices = self.employee_list.curselection()
        return self.data['employees'][indices[0]] if indices else None

    def selected_laser(self):
        indices = self.laser_list.curselection()
        return self.data['lasers'][indices[0]] if indices else None

    def assign_selected(self):
        employee, laser = self.selected_employee(), self.selected_laser()
        if not employee or not laser:
            messagebox.showinfo('Asignación', 'Selecciona un empleado y una láser.')
            return
        self.job(lambda: self.api.call('/assign', code=employee['code'], laser_id=laser['id']), lambda _: self.refresh())

    def unassign(self):
        employee = self.selected_employee()
        if employee:
            self.job(lambda: self.api.call('/assign', code=employee['code'], laser_id=None), lambda _: self.refresh())

    def toggle_laser(self):
        laser = self.selected_laser()
        if not laser:
            messagebox.showinfo('Láser', 'Selecciona una láser.')
            return
        active = not bool(laser.get('active', 1))
        if not active and not messagebox.askyesno('Desactivar láser', 'Se quitarán sus asignaciones y se conservará el historial. ¿Continuar?'):
            return
        self.job(lambda: self.api.call('/laser-active', laser_id=laser['id'], active=active), lambda _: self.refresh())

    def add_employee(self):
        code = simpledialog.askstring('Nuevo empleado', 'Código de empleado:', parent=self)
        if not code:
            return
        name = simpledialog.askstring('Nuevo empleado', 'Nombre:', parent=self)
        if name:
            self.job(lambda: self.api.call('/employee', code=code, name=name), lambda _: self.refresh())

    def add_laser(self):
        self.laser_dialog(None)

    def edit_laser(self):
        laser = self.selected_laser()
        if laser:
            self.laser_dialog(laser)
        else:
            messagebox.showinfo('Láser', 'Selecciona una láser de la lista.')

    def laser_dialog(self, laser):
        name = simpledialog.askstring('Láser', 'Nombre de la láser:', initialvalue=laser['name'] if laser else '', parent=self)
        if not name:
            return
        account = simpledialog.askstring('Cuenta CPA', 'Cuenta CPA de referencia (sin contraseña):', initialvalue=laser['cpa_account'] if laser else '', parent=self)
        if account is not None:
            self.job(lambda: self.api.call('/laser', name=name, account=account, laser_id=laser['id'] if laser else None), lambda _: self.refresh())

    def import_cpa(self):
        path = filedialog.askopenfilename(title='Base CPA de Hermes', filetypes=[('Base CPA', '*.db *.sqlite *.sqlite3 *.csv *.xlsx')])
        if not path:
            return
        if not messagebox.askyesno('Actualizar CPA', 'Esto reemplazará la base de validación y recalculará las alertas existentes. ¿Continuar?'):
            return
        def work():
            return self.api.call('/cpa', rows=read_cpa(path))
        def done(result):
            messagebox.showinfo('CPA actualizado', f"{result['count']:,} líneas cargadas.")
            self.refresh()
        self.job(work, done)

    def sync(self):
        self.job(lambda: self.api.call('/sync'), lambda _: self.status.set('Sincronización solicitada. El estado se actualizará automáticamente.'))

    def make_charts(self):
        self.summary = tk.StringVar()
        ttk.Label(self.chart_tab, textvariable=self.summary, foreground=GREEN, font=('Segoe UI', 15, 'bold')).pack(anchor='w', pady=10)
        ttk.Label(self.chart_tab, text='Piezas reportadas hoy por láser · incluye registros con incidencias', foreground=MUTED).pack(anchor='w')
        self.chart = tk.Canvas(self.chart_tab, bg=PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(self.chart_tab, orient='vertical', command=self.chart.yview)
        scroll.pack(side='right', fill='y')
        self.chart.configure(yscrollcommand=scroll.set)
        self.chart.pack(fill='both', expand=True, pady=15)
        self.chart.bind('<Configure>', lambda e: self.draw_chart())

    def draw_chart(self):
        if not self.data or not self.supervisor:
            return
        self.chart.delete('all')
        today = [r for r in self.data['reports'] if r['report_date'] == self.data['today']]
        totals = [(l['name'], sum(r['qty'] for r in today if r['laser_id'] == l['id'])) for l in self.data['lasers']]
        if not totals:
            self.chart.create_text(30, 30, text='Crea las láseres para empezar.', fill=GREEN, anchor='nw')
            return
        maximum = max(1, *(v for _, v in totals))
        width = max(250, self.chart.winfo_width() - 260)
        for i, (name, total) in enumerate(totals):
            y = 25 + i * 48
            self.chart.create_text(15, y + 12, text=name, fill=TEXT, anchor='w')
            end = 170 + width * total / maximum
            self.chart.create_rectangle(170, y, max(172, end), y + 25, fill=GREEN, outline='')
            self.chart.create_text(end + 12, y + 12, text=f'{total:,}', fill=TEXT, anchor='w')
        self.chart.configure(scrollregion=(0, 0, width + 260, len(totals) * 48 + 40))
        self.summary.set(f"HOY  /  {sum(r['qty'] for r in today):,} piezas   ·   {len(today):,} registros   ·   {sum(r['duplicate'] for r in today)} filas duplicadas   ·   {sum(bool(r['alert']) for r in today)} incidencias")

    def paint(self, data):
        previous_employee = self.selected_employee() if self.supervisor and self.data and self.employee_list.size() else None
        previous_laser = self.selected_laser() if self.supervisor and self.data and self.laser_list.size() else None
        self.data = data
        user = data['user']
        if not self.supervisor and (user['laser_id'] is None or not data['lasers']):
            self.capture.pack_forget()
            self.waiting.place(relx=.5, rely=.45, anchor='center')
            self.waiting.lift()
        else:
            self.waiting.place_forget()
            if not self.capture.winfo_manager():
                self.capture.pack(fill='x', after=self.report_tab.winfo_children()[0])
        today = [r for r in data['reports'] if r['report_date'] == data['today']]
        self.metrics.set(f"HOY  {sum(r['qty'] for r in today):,} piezas  /  {len(today)} registros  /  {sum(bool(r['alert']) for r in today)} incidencias")
        if self.supervisor:
            names = [r['name'] for r in data['lasers'] if r.get('active', 1)]
            self.report_laser['values'] = names
            if self.laser_choice.get() not in names:
                self.laser_choice.set(names[0] if names else '')
            self.filter_box['values'] = ['Todas'] + names
            if self.filter_laser.get() not in ['Todas'] + names:
                self.filter_laser.set('Todas')
            selected_employee = previous_employee
            selected_laser = previous_laser
            self.employee_list.delete(0, 'end')
            self.laser_list.delete(0, 'end')
            mapping = {r['id']: r['name'] for r in data['lasers']}
            for i, employee in enumerate(data['employees']):
                self.employee_list.insert('end', f"{employee['code']}  /  {employee['name']}  →  {mapping.get(employee['laser_id'], 'Sin asignar')}")
                if selected_employee and selected_employee['code'] == employee['code']:
                    self.employee_list.selection_set(i)
            for i, laser in enumerate(data['lasers']):
                count = sum(e['laser_id'] == laser['id'] for e in data['employees'])
                self.laser_list.insert('end', f"{laser['name']} {'[INACTIVA]' if not laser.get('active', 1) else ''} / {count} operadores / CPA: {laser['cpa_account'] or 'sin cuenta'}")
                if selected_laser and selected_laser['id'] == laser['id']:
                    self.laser_list.selection_set(i)
            self.draw_chart()
        self.paint_rows()
        sync = data['sync']
        state = sync['sync_error'] or ('Pendiente de sincronizar con Sheets' if sync['dirty'] == '1' else 'Sheets sincronizado')
        self.status.set(f"{state}  ·  Última sincronización: {sync['sync_at'] or 'ninguna'}  ·  CPA: {data['cpa_lines']:,} líneas / {data['cpa_updated']}")

    def paint_rows(self):
        self.table.delete(*self.table.get_children())
        for r in self.data['reports']:
            if self.only_today.get() and r['report_date'] != self.data['today']:
                continue
            if self.only_alerts.get() and not r['alert']:
                continue
            if self.supervisor and self.filter_laser.get() not in ('Todas', r['laser']):
                continue
            self.table.insert('', 'end', iid=str(r['id']), values=[r[k] for k in ('report_date', 'laser', 'ord', 'line', 'qty', 'employee', 'comment', 'alert')], tags=(r['color'],))

    def show_detail(self, event=None):
        selection = self.table.selection()
        if selection:
            row = next(r for r in self.data['reports'] if str(r['id']) == selection[0])
            messagebox.showinfo(f"{row['ord']} | {row['line']}", f"{row['laser']} · {row['qty']} piezas\nEmpleado: {row['employee']}\nReprint: {'Sí' if row['reprint'] else 'No'}\n\n{row['comment']}\n\n{row['alert'] or 'Sin incidencias'}")

    def refresh(self):
        self.job(lambda: self.api.call('/snapshot'), self.paint)

    def poll(self):
        if self.data is None:
            return
        self.job(lambda: self.api.call('/snapshot'), self.paint, quiet=True)
        self.poll_id = self.after(5000, self.poll)


def main():
    Desktop().mainloop()


if __name__ == '__main__':
    main()
