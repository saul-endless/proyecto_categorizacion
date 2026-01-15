# gui_categorizador.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import tkinter.font as tkfont # Se importa font para medir texto
import os
import sys
import shutil
import json
import time
import threading
import importlib.util
import gc
import re 
from pathlib import Path

# Se definen las configuraciones y rutas del sistema
BASE_DIR = Path("/home/endless/FUNCIONALIDADES")
DIR_CATEGORIZACION = BASE_DIR / "PROYECTO CATEGORIZACION"
DIR_EXTRACTOR = BASE_DIR / "PROYECTO EXTRACTOR"
DIR_MODELOS = BASE_DIR / "PRUEBA MODELOS"

ORQ_INPUT = DIR_CATEGORIZACION / "input"
ORQ_OUTPUT = DIR_CATEGORIZACION / "output"
EXT_INPUT = DIR_EXTRACTOR / "input"
EXT_OUTPUT = DIR_EXTRACTOR / "output"
MOD_INPUT = DIR_MODELOS / "INPUT"
MOD_OUTPUT = DIR_MODELOS / "OUTPUT"

# Se define la lista de categorías disponibles
LISTA_CATEGORIAS = [
    "1. Insumos y Producción (COGS)", "2. Logística y Distribución", "3. Tecnología y Digital (IT)",
    "4. Viajes Corporativos", "5. Infraestructura Física y Mto", "6. Operación de Oficina",
    "7. Marketing y Ventas", "8. Servicios Profesionales", "9. Servicios Básicos y Generales",
    "10. Recursos Humanos", "11. Financiero y Fiscal", "12. Activos e Inversión (CAPEX)",
    "13. Gastos No Deducibles / Personales", "OTRO"
]

# Se definen las funciones auxiliares
def limpiar_directorio(directorio):
    if directorio.exists():
        for archivo in directorio.glob("*"):
            if archivo.is_file(): archivo.unlink()

def obtener_ultimo_archivo(directorio, patron="*"):
    archivos = list(directorio.glob(patron))
    return max(archivos, key=os.path.getctime) if archivos else None

def cargar_modulo_con_espacios(ruta_archivo, nombre_modulo):
    spec = importlib.util.spec_from_file_location(nombre_modulo, ruta_archivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre_modulo] = modulo
    spec.loader.exec_module(modulo)
    return modulo

# Se define la clase para redirigir la salida estándar y actualizar el progreso
class StdoutRedirector:
    def __init__(self, text_widget, progress_bar, root):
        self.text_widget = text_widget
        self.progress_bar = progress_bar
        self.root = root
        self.original_stdout = sys.stdout

    def write(self, text):
        self.root.after(0, lambda: self._update_text(text))
        match = re.search(r"Lote\s+(\d+)/(\d+)", text)
        if match:
            try:
                actual = int(match.group(1))
                total = int(match.group(2))
                if total > 0:
                    porcentaje = (actual / total) * 100
                    self.root.after(0, lambda: self._update_progress(porcentaje))
            except:
                pass

    def _update_text(self, text):
        self.text_widget.insert(tk.END, text)
        self.text_widget.see(tk.END)

    def _update_progress(self, val):
        self.progress_bar['value'] = val

    def flush(self):
        pass

# Se define la clase principal de la aplicación
class CategorizadorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Orquestador Financiero - Gestión Total Multi-Periodo")
        self.root.geometry("1400x950")
        
        # Se configura el estilo visual de la interfaz
        style = ttk.Style()
        style.theme_use("clam") 
        # Se ajusta la altura de las filas
        style.configure("Treeview", rowheight=40, font=('Arial', 9)) 
        style.configure("Treeview.Heading", font=('Arial', 10, 'bold'))
        
        self.font_medida = tkfont.Font(family="Arial", size=9)
        self.font_header = tkfont.Font(family="Arial", size=10, weight="bold")

        # Se inicializa la estructura de datos
        self.master_data = {} 
        self.periodo_actual = None
        self.vista_actual = "ingresos" 
        self.modo_json_directo = False
        self.archivos_seleccionados_manual = []
        self.archivos_para_fase3 = []
        
        # Se definen las variables de control por fase
        self.periodo_actual_f2 = None
        self.vista_actual_f2 = "ingresos"
        self.entries_datos_generales_f2 = {}
        
        self.periodo_actual_f3 = None
        self.entries_datos_generales_f3 = {}

        # Se crean los directorios necesarios
        ORQ_INPUT.mkdir(parents=True, exist_ok=True)
        ORQ_OUTPUT.mkdir(parents=True, exist_ok=True)
        MOD_INPUT.mkdir(parents=True, exist_ok=True)

        # Se configuran las pestañas de la interfaz
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.tab_carga = ttk.Frame(self.notebook)
        self.tab_fase1 = ttk.Frame(self.notebook)
        self.tab_fase2 = ttk.Frame(self.notebook)
        self.tab_fase3 = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_carga, text="Carga de Archivos")
        self.notebook.add(self.tab_fase1, text="Fase 1: Extracción")
        self.notebook.add(self.tab_fase2, text="Fase 2: Categorización")
        self.notebook.add(self.tab_fase3, text="Fase 3: Perfilado")

        self._construir_tab_carga()
        self._construir_tab_fase1()
        self._construir_tab_fase2()
        self._construir_tab_fase3()

    # Se construye la pestaña de carga de archivos
    def _construir_tab_carga(self):
        frame_controles = ttk.LabelFrame(self.tab_carga, text="Gestión de Archivos")
        frame_controles.pack(pady=20, padx=20, fill='x')
        
        # Se configura la opción de carga de PDF
        frame_pdf = ttk.Frame(frame_controles)
        frame_pdf.pack(fill='x', pady=10)
        ttk.Label(frame_pdf, text="Opción A (Proceso Completo):").pack(side='left', padx=10)
        ttk.Button(frame_pdf, text="Subir Estados de Cuenta (.PDF)", command=self.cargar_pdfs).pack(side='left', padx=10)
        
        # Se configura la opción de carga de JSON
        frame_json = ttk.Frame(frame_controles)
        frame_json.pack(fill='x', pady=10)
        ttk.Label(frame_json, text="Opción B (Carga Directa):").pack(side='left', padx=10)
        
        ttk.Button(frame_json, text="Subir JSONs (Todas las Fases)", command=self.cargar_jsons).pack(side='left', padx=10)
        ttk.Label(frame_json, text="(Soporta: _DATOS, _INGRESOS, _EGRESOS, _PERFIL, _MODIFICADO)", font=("Arial", 8, "italic")).pack(side='left')
        
        ttk.Button(frame_controles, text="Limpiar Todo", command=self.limpiar_todo).pack(side='right', padx=20)
        
        self.lista_archivos = tk.Listbox(self.tab_carga, width=120, height=20)
        self.lista_archivos.pack(pady=10, padx=20)
        limpiar_directorio(ORQ_INPUT)

    def cargar_pdfs(self):
        archivos = filedialog.askopenfilenames(filetypes=[("PDF Files", "*.pdf")])
        if archivos:
            self.modo_json_directo = False
            self.archivos_seleccionados_manual = []
            for f in ORQ_INPUT.glob("*.json"): f.unlink()
            for archivo in archivos:
                shutil.copy(archivo, ORQ_INPUT / Path(archivo).name)
                self.lista_archivos.insert(tk.END, f"[PDF] {Path(archivo).name}")

    def cargar_jsons(self):
        archivos = filedialog.askopenfilenames(filetypes=[("All Files", "*.*"), ("JSON Files", "*.json")])
        
        if archivos:
            self.modo_json_directo = True
            for f in ORQ_INPUT.glob("*.pdf"): f.unlink()
            
            if not self.archivos_seleccionados_manual: 
                self.archivos_seleccionados_manual = []

            for archivo in archivos:
                path_archivo = Path(archivo)
                if path_archivo.suffix.lower() == '.json':
                    shutil.copy(archivo, ORQ_INPUT / path_archivo.name)
                    
                    if path_archivo.name not in self.archivos_seleccionados_manual:
                        self.lista_archivos.insert(tk.END, f"[JSON] {path_archivo.name}")
                        self.archivos_seleccionados_manual.append(path_archivo.name)

    def limpiar_todo(self):
        limpiar_directorio(ORQ_INPUT)
        self.lista_archivos.delete(0, tk.END)
        self.master_data = {}
        self.modo_json_directo = False
        self.archivos_seleccionados_manual = []

    # Se construye la pestaña de la fase 1
    def _construir_tab_fase1(self):
        frame_top = ttk.Frame(self.tab_fase1)
        frame_top.pack(side='top', fill='x', pady=5, padx=10)
        
        self.btn_accion = ttk.Button(frame_top, text="Procesar Archivos Cargados", command=self.ejecutar_fase_1)
        self.btn_accion.pack(side='left')
        self.progress_fase1 = ttk.Progressbar(frame_top, mode='indeterminate')
        self.progress_fase1.pack(side='left', fill='x', expand=True, padx=10)
        ttk.Button(frame_top, text="Guardar Todo y Pasar a Fase 2 >", command=self.ir_a_fase_2).pack(side='right')

        # Se configura el selector de estados de cuenta
        frame_selector = ttk.LabelFrame(self.tab_fase1, text="Selección de Estado de Cuenta")
        frame_selector.pack(side='top', fill='x', padx=10, pady=5)
        ttk.Label(frame_selector, text="Periodo:").pack(side='left', padx=5)
        self.combo_periodos = ttk.Combobox(frame_selector, state="readonly", width=50)
        self.combo_periodos.pack(side='left', padx=5)
        self.combo_periodos.bind("<<ComboboxSelected>>", self._cambiar_periodo)

        # Se configuran los botones de alternancia
        frame_toggle = ttk.Frame(self.tab_fase1)
        frame_toggle.pack(side='top', fill='x', padx=10, pady=5)
        self.btn_ver_ingresos = ttk.Button(frame_toggle, text="VER INGRESOS", command=lambda: self._toggle_tabla("ingresos"))
        self.btn_ver_ingresos.pack(side='left', fill='x', expand=True, padx=2)
        self.btn_ver_egresos = ttk.Button(frame_toggle, text="VER EGRESOS", command=lambda: self._toggle_tabla("egresos"))
        self.btn_ver_egresos.pack(side='left', fill='x', expand=True, padx=2)

        # Se configuran las tablas de datos
        self.frame_tablas = ttk.Frame(self.tab_fase1)
        self.frame_tablas.pack(side='top', fill='both', expand=True, padx=10)
        self.cols_f1 = [
            "Fecha de la transacción", "Nombre de la transacción", "Nombre resumido", 
            "Tipo de transacción", "Clasificación", "Quien realiza o recibe el pago",
            "Monto de la transacción", "Numero de referencia o folio", 
            "Numero de cuenta origen", "Numero de cuenta destino", 
            "Metodo de pago", "Sucursal o ubicacion"
        ]
        self.tree_ingresos = self._crear_treeview_fase1(self.frame_tablas)
        self.tree_egresos = self._crear_treeview_fase1(self.frame_tablas)
        self.tree_ingresos.pack(fill='both', expand=True)

        # Se configura la sección de datos generales
        self.frame_datos_gen = ttk.LabelFrame(self.tab_fase1, text="Datos Generales del Periodo (Editables)")
        self.frame_datos_gen.pack(side='bottom', fill='x', padx=10, pady=10)
        self.entries_datos_generales = {}

    def _crear_treeview_fase1(self, parent):
        tree = ttk.Treeview(parent, columns=self.cols_f1, show='headings', selectmode="browse")
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        tree.pack(side='left', fill='both', expand=True)

        for col in self.cols_f1:
            tree.heading(col, text=col)
            # Se deshabilita el estiramiento automático de columnas
            tree.column(col, width=120, minwidth=100, stretch=False)
        tree.bind("<Double-1>", self.editar_celda_fase1)
        return tree

    def ejecutar_fase_1(self):
        self.progress_fase1.start(10)
        threading.Thread(target=self._logica_fase_1).start()

    def _logica_fase_1(self):
        lista_final_archivos = []

        if not self.modo_json_directo:
            print("Iniciando Extracción de PDFs...")
            pdfs = list(ORQ_INPUT.glob("*.pdf"))
            if not pdfs:
                print("No hay PDFs.")
                self.root.after(0, self.progress_fase1.stop)
                return

            EXT_INPUT.mkdir(parents=True, exist_ok=True)
            EXT_OUTPUT.mkdir(parents=True, exist_ok=True)
            limpiar_directorio(EXT_INPUT)
            limpiar_directorio(EXT_OUTPUT)
            
            for pdf in pdfs:
                shutil.copy(pdf, EXT_INPUT / pdf.name)
            
            sys.path.append(str(DIR_EXTRACTOR))
            try:
                import main_extractor # type: ignore
                os.chdir(DIR_EXTRACTOR)
                main_extractor.main()
            except Exception as e:
                print(f"Error extractor: {e}")
                self.root.after(0, self.progress_fase1.stop)
                return
            
            for f in EXT_OUTPUT.glob("*.json"):
                # Se normaliza el nombre a mayúsculas conservando la extensión
                nombre_base = f.stem.upper()
                destino = ORQ_OUTPUT / f"{nombre_base}.json"
                shutil.move(str(f), str(destino))
                lista_final_archivos.append(destino)
        else:
            print(f"Cargando {len(self.archivos_seleccionados_manual)} JSONs seleccionados...")
            for nombre in self.archivos_seleccionados_manual:
                src = ORQ_INPUT / nombre
                dest = ORQ_OUTPUT / nombre
                shutil.copy(src, dest)
                lista_final_archivos.append(dest)

        self._procesar_resultados_en_memoria(lista_final_archivos)
        self.root.after(0, self._inicializar_ui_datos)
        self.root.after(0, self._inicializar_ui_fase2)
        self.root.after(0, self._actualizar_ui_f3)
        self.root.after(0, self.progress_fase1.stop)

    def _procesar_resultados_en_memoria(self, lista_archivos_validos):
        self.master_data = {}
        
        # Se buscan los archivos de datos generales
        archivos_datos = [f for f in lista_archivos_validos if "_DATOS" in f.name.upper()]
        
        for ruta_datos in archivos_datos:
            try:
                with open(ruta_datos, 'r', encoding='utf-8') as f:
                    data_gen = json.load(f)
                periodo_key = data_gen.get("Periodo del estado de cuenta", "DESCONOCIDO")
                
                # Se aplica expresión regular para limpiar nombres
                nombre_clean = ruta_datos.name
                nombre_clean = re.sub(r'(_DATOS|_MODIFICADO|_PERFIL)*\.json$', '', nombre_clean, flags=re.IGNORECASE)
                
                self.master_data[periodo_key] = {
                    "datos_gen": data_gen,
                    "ingresos": [],
                    "egresos": [],
                    "cat_ingresos": [],
                    "cat_egresos": [],
                    "perfil": {},
                    "rutas": {"datos": ruta_datos},
                    "nombre_base_match": nombre_clean
                }
            except Exception as e:
                print(f"Error: {e}")

        # Se buscan transacciones e información adicional
        for periodo, estructura in self.master_data.items():
            match_str = estructura["nombre_base_match"]
            
            for f in lista_archivos_validos:
                if match_str in f.name:
                    nombre_upper = f.name.upper()
                    
                    # Se cargan los perfiles correspondientes a la fase 3
                    if "_PERFIL" in nombre_upper:
                        try:
                            with open(f, 'r', encoding='utf-8') as fp:
                                estructura["perfil"] = json.load(fp)
                        except: pass
                        continue

                    # Se cargan las transacciones correspondientes a las fases 1 y 2
                    if "_DATOS" not in nombre_upper: 
                        if "_CON_GIROS" not in nombre_upper:
                            # Se procesan archivos de fase 1
                            if "_INGRESOS" in nombre_upper:
                                with open(f, 'r', encoding='utf-8') as fi:
                                    estructura["ingresos"] = json.load(fi)
                                    estructura["rutas"]["ingresos"] = f
                            elif "_EGRESOS" in nombre_upper:
                                with open(f, 'r', encoding='utf-8') as fe:
                                    estructura["egresos"] = json.load(fe)
                                    estructura["rutas"]["egresos"] = f
                        else:
                            # Se procesan archivos de fase 2
                            if "_INGRESOS" in nombre_upper:
                                with open(f, 'r', encoding='utf-8') as fi:
                                    estructura["cat_ingresos"] = json.load(fi)
                                    estructura["rutas"]["cat_ingresos"] = f
                            elif "_EGRESOS" in nombre_upper:
                                with open(f, 'r', encoding='utf-8') as fe:
                                    estructura["cat_egresos"] = json.load(fe)
                                    estructura["rutas"]["cat_egresos"] = f

    def _inicializar_ui_datos(self):
        periodos = list(self.master_data.keys())
        self.combo_periodos['values'] = periodos
        self.combo_periodos_f2['values'] = periodos
        self.combo_periodos_f3['values'] = periodos

        if periodos:
            self.combo_periodos.current(0)
            self._cambiar_periodo(None)
        else:
            if self.modo_json_directo:
                 messagebox.showwarning("Atención", "No se detectaron archivos '_DATOS' válidos.\nAsegúrate de subir el trío completo.")

    def _cambiar_periodo(self, event):
        periodo = self.combo_periodos.get()
        if not periodo or periodo not in self.master_data: return
        self.periodo_actual = periodo
        data = self.master_data[periodo]
        self._construir_form_datos_generales(self.frame_datos_gen, data["datos_gen"], self.entries_datos_generales)
        self._llenar_treeview(self.tree_ingresos, data["ingresos"], self.cols_f1)
        self._llenar_treeview(self.tree_egresos, data["egresos"], self.cols_f1)
        self._toggle_tabla(self.vista_actual)

    def _ajustar_columnas(self, tree, cols, lista_datos):
        # Se ajusta el ancho de las columnas basado en el contenido
        if not lista_datos: return
        
        for col in cols:
            # Se calcula el ancho del encabezado
            ancho_max = self.font_header.measure(col) + 20
            
            # Se revisa el ancho de los datos limitando a los primeros 100 registros
            for item in lista_datos[:100]:
                val = str(item.get(col, ""))
                ancho_item = self.font_medida.measure(val) + 20
                if ancho_item > ancho_max:
                    ancho_max = ancho_item
            
            # Se establece un ancho máximo
            if ancho_max > 800: ancho_max = 800
            
            # Se aplica el ajuste de columna
            tree.column(col, width=ancho_max, stretch=False)

    def _llenar_treeview(self, tree, lista_datos, columnas):
        tree.delete(*tree.get_children())
        if lista_datos:
            for idx, item in enumerate(lista_datos):
                valores = [item.get(col, "") for col in columnas]
                tree.insert("", "end", iid=idx, values=valores)
            
            # Se aplica el ajuste automático de columnas
            self._ajustar_columnas(tree, columnas, lista_datos)

    def _construir_form_datos_generales(self, frame_padre, datos, diccionario_entries):
        for widget in frame_padre.winfo_children(): widget.destroy()
        diccionario_entries.clear()
        
        campos = [
            "Nombre de la empresa del estado de cuenta", "Numero de cuenta del estado de cuenta",
            "Periodo del estado de cuenta", "Saldo inicial de la cuenta",
            "Saldo final de la cuenta", "Saldo promedio del periodo",
            "Cantidad total de depositos", "Cantidad total de retiros"
        ]
        r, c = 0, 0
        for campo in campos:
            ttk.Label(frame_padre, text=campo+":", font=('Arial', 8, 'bold')).grid(row=r, column=c, sticky='e', padx=5, pady=2)
            entry = ttk.Entry(frame_padre, width=25)
            entry.insert(0, str(datos.get(campo, "")))
            entry.grid(row=r, column=c+1, sticky='w', padx=5, pady=2)
            diccionario_entries[campo] = entry
            c += 2
            if c >= 4:
                c = 0
                r += 1

    def _toggle_tabla(self, vista):
        self.vista_actual = vista
        self.tree_ingresos.pack_forget()
        self.tree_egresos.pack_forget()
        if vista == "ingresos":
            self.tree_ingresos.pack(fill='both', expand=True)
            self.btn_ver_ingresos.state(['pressed'])
            self.btn_ver_egresos.state(['!pressed'])
        else:
            self.tree_egresos.pack(fill='both', expand=True)
            self.btn_ver_ingresos.state(['!pressed'])
            self.btn_ver_egresos.state(['pressed'])

    def editar_celda_fase1(self, event):
        tree = self.tree_ingresos if self.vista_actual == "ingresos" else self.tree_egresos
        sel = tree.selection()
        if not sel: return
        item_id = sel[0]
        col_id = tree.identify_column(event.x)
        idx_col = int(col_id.replace('#', '')) - 1
        col_nombre = self.cols_f1[idx_col]
        
        lista_ref = self.master_data[self.periodo_actual][self.vista_actual]
        valor_actual = lista_ref[int(item_id)].get(col_nombre, "")
        
        nuevo = simpledialog.askstring("Editar", f"Valor para {col_nombre}:", initialvalue=str(valor_actual))
        if nuevo is not None:
            lista_ref[int(item_id)][col_nombre] = nuevo
            vals = list(tree.item(item_id, 'values'))
            vals[idx_col] = nuevo
            tree.item(item_id, values=vals)

    def ir_a_fase_2(self):
        self._guardar_datos_generales_ui_en_memoria(self.periodo_actual, self.entries_datos_generales)
        rutas_generadas = []
        for periodo, data in self.master_data.items():
            for tipo in ["ingresos", "egresos", "datos"]:
                key_lista = "datos_gen" if tipo == "datos" else tipo
                if data.get(key_lista) and data["rutas"].get(tipo):
                    ruta_orig = Path(data["rutas"][tipo])
                    nombre = ruta_orig.stem
                    if "_MODIFICADO" not in nombre: nombre += "_MODIFICADO"
                    
                    nueva_ruta = ORQ_OUTPUT / (nombre + ".json")
                    with open(nueva_ruta, 'w', encoding='utf-8') as f:
                        json.dump(data[key_lista], f, indent=4, ensure_ascii=False)
                    if tipo != "datos": rutas_generadas.append(nueva_ruta)

        self.archivos_para_fase2 = rutas_generadas
        print(f"Datos guardados. {len(rutas_generadas)} archivos transaccionales para Fase 2.")
        self.notebook.select(self.tab_fase2)
        self._inicializar_ui_fase2()

    def _guardar_datos_generales_ui_en_memoria(self, periodo, entries_dict):
        if not periodo or periodo not in self.master_data: return
        data_gen = self.master_data[periodo]["datos_gen"]
        for campo, entry in entries_dict.items():
            val = entry.get()
            if "Saldo" in campo or "Cantidad" in campo:
                try: val = float(val)
                except: pass
            data_gen[campo] = val

    # Se construye la pestaña de la fase 2
    def _construir_tab_fase2(self):
        frame_top = ttk.Frame(self.tab_fase2)
        frame_top.pack(fill='x', pady=10, padx=10)
        
        self.progress_f2 = ttk.Progressbar(frame_top, mode='determinate')
        self.progress_f2.pack(side='bottom', fill='x', pady=5)

        ttk.Button(frame_top, text="Iniciar Categorización (GPU)", command=self.ejecutar_fase_2).pack(side='left')
        self.txt_logs = tk.Text(frame_top, height=5, width=50, bg="#f0f0f0", font=("Consolas", 8))
        self.txt_logs.pack(side='left', padx=10, fill='x', expand=True)
        ttk.Button(frame_top, text="Guardar y Pasar a Fase 3 >", command=self.ir_a_fase_3).pack(side='right')

        frame_selector = ttk.LabelFrame(self.tab_fase2, text="Selección de Periodo (Resultados)")
        frame_selector.pack(side='top', fill='x', padx=10, pady=5)
        ttk.Label(frame_selector, text="Periodo:").pack(side='left', padx=5)
        self.combo_periodos_f2 = ttk.Combobox(frame_selector, state="readonly", width=50)
        self.combo_periodos_f2.pack(side='left', padx=5)
        self.combo_periodos_f2.bind("<<ComboboxSelected>>", self._cambiar_periodo_f2)

        frame_toggle = ttk.Frame(self.tab_fase2)
        frame_toggle.pack(side='top', fill='x', padx=10, pady=5)
        self.btn_ver_ingresos_f2 = ttk.Button(frame_toggle, text="VER INGRESOS CATEGORIZADOS", command=lambda: self._toggle_tabla_f2("ingresos"))
        self.btn_ver_ingresos_f2.pack(side='left', fill='x', expand=True, padx=2)
        self.btn_ver_egresos_f2 = ttk.Button(frame_toggle, text="VER EGRESOS CATEGORIZADOS", command=lambda: self._toggle_tabla_f2("egresos"))
        self.btn_ver_egresos_f2.pack(side='left', fill='x', expand=True, padx=2)

        self.frame_tablas_f2 = ttk.Frame(self.tab_fase2)
        self.frame_tablas_f2.pack(side='top', fill='both', expand=True, padx=10)
        
        self.cols_f2 = [
            "Fecha de la transacción", "Nombre de la transacción", "Nombre resumido", "Tipo de transacción", "Clasificación",
            "Quien realiza o recibe el pago", "Monto de la transacción", "Numero de referencia o folio", 
            "Numero de cuenta origen", "Numero de cuenta destino", "Metodo de pago", "Sucursal o ubicacion",
            "Giro de la transacción", "Giro sugerido", "Análisis monto", "Análisis contraparte", 
            "Análisis naturaleza", "Detalle de la operación", "Perfil Industrial Contraparte", "Inferencia de Negocio"
        ]
        
        self.tree_f2_ing = self._crear_treeview_f2(self.frame_tablas_f2)
        self.tree_f2_egr = self._crear_treeview_f2(self.frame_tablas_f2)
        self.tree_f2_ing.pack(fill='both', expand=True)

        self.frame_datos_gen_f2 = ttk.LabelFrame(self.tab_fase2, text="Datos Generales del Periodo (Editables)")
        self.frame_datos_gen_f2.pack(side='bottom', fill='x', padx=10, pady=10)

    def _crear_treeview_f2(self, parent):
        tree = ttk.Treeview(parent, columns=self.cols_f2, show='headings', selectmode="browse")
        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        tree.pack(side='left', fill='both', expand=True)

        for col in self.cols_f2:
            tree.heading(col, text=col)
            # Se deshabilita el estiramiento automático de columnas
            tree.column(col, width=120, minwidth=100, stretch=False)
        tree.bind("<Double-1>", self.editar_celda_fase2)
        return tree

    def ejecutar_fase_2(self):
        threading.Thread(target=self._logica_fase_2).start()

    def _logica_fase_2(self):
        self._log_fase2("Iniciando Fase 2...")
        MOD_INPUT.mkdir(parents=True, exist_ok=True)
        MOD_OUTPUT.mkdir(parents=True, exist_ok=True)
        
        original_stdout = sys.stdout
        sys.stdout = StdoutRedirector(self.txt_logs, self.progress_f2, self.root)

        try:
            ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
            mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
            if 'CUDA_VISIBLE_DEVICES' in os.environ: del os.environ['CUDA_VISIBLE_DEVICES']
            os.chdir(DIR_MODELOS)
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
            if not hasattr(self, 'archivos_para_fase2') or not self.archivos_para_fase2: 
                print("Sin archivos.")
                return

            for ruta in self.archivos_para_fase2:
                print(f"Procesando: {ruta.name}")
                shutil.copy(ruta, MOD_INPUT / ruta.name)
                self.root.after(0, lambda: self.progress_f2.config(value=0))
                
                mod.procesar_logica_fusion(llm, ruta.name)
                
                nombre_base = ruta.stem
                patron = f"ANALISIS_{nombre_base}_*.json"
                res = obtener_ultimo_archivo(MOD_OUTPUT, patron)
                
                if res:
                    nombre_final = f"{nombre_base}_CON_GIROS".upper() + ".json"
                    dest = ORQ_OUTPUT / nombre_final
                    shutil.move(str(res), str(dest))
                    print(f"OK: {dest.name}")
                    
                    with open(dest, 'r') as f:
                        data_cat = json.load(f)
                    
                    for periodo, struct in self.master_data.items():
                        match_str = struct["nombre_base_match"]
                        if match_str in dest.name:
                            if "_INGRESOS" in dest.name:
                                struct["cat_ingresos"] = data_cat
                            elif "_EGRESOS" in dest.name:
                                struct["cat_egresos"] = data_cat
            del llm
            gc.collect()
            self.root.after(0, self._inicializar_ui_fase2)
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            sys.stdout = original_stdout 

    def _inicializar_ui_fase2(self):
        periodos = list(self.master_data.keys())
        self.combo_periodos_f2['values'] = periodos
        if periodos:
            self.combo_periodos_f2.current(0)
            self._cambiar_periodo_f2(None)

    def _cambiar_periodo_f2(self, event):
        periodo = self.combo_periodos_f2.get()
        if not periodo or periodo not in self.master_data: return
        self.periodo_actual_f2 = periodo
        data = self.master_data[periodo]
        
        self._construir_form_datos_generales(self.frame_datos_gen_f2, data["datos_gen"], self.entries_datos_generales_f2)
        self._llenar_treeview(self.tree_f2_ing, data["cat_ingresos"], self.cols_f2)
        self._llenar_treeview(self.tree_f2_egr, data["cat_egresos"], self.cols_f2)
        self._toggle_tabla_f2(self.vista_actual_f2)

    def _toggle_tabla_f2(self, vista):
        self.vista_actual_f2 = vista
        self.tree_f2_ing.pack_forget()
        self.tree_f2_egr.pack_forget()
        if vista == "ingresos":
            self.tree_f2_ing.pack(fill='both', expand=True)
            self.btn_ver_ingresos_f2.state(['pressed'])
            self.btn_ver_egresos_f2.state(['!pressed'])
        else:
            self.tree_f2_egr.pack(fill='both', expand=True)
            self.btn_ver_ingresos_f2.state(['!pressed'])
            self.btn_ver_egresos_f2.state(['pressed'])

    def _log_fase2(self, msg):
        self.txt_logs.insert(tk.END, msg+"\n")
        self.txt_logs.see(tk.END)

    def editar_celda_fase2(self, event):
        tree = self.tree_f2_ing if self.vista_actual_f2 == "ingresos" else self.tree_f2_egr
        sel = tree.selection()
        if not sel: return
        item_id = sel[0]
        
        col_id = tree.identify_column(event.x)
        idx = int(col_id.replace('#', '')) - 1
        clave = self.cols_f2[idx]
        
        key_list = "cat_ingresos" if self.vista_actual_f2 == "ingresos" else "cat_egresos"
        lista_ref = self.master_data[self.periodo_actual_f2][key_list]
        
        if clave == "Giro de la transacción":
            self._popup_menu_giros(tree, item_id, lista_ref, clave, idx)
        else:
            curr = lista_ref[int(item_id)].get(clave, "")
            nuevo = simpledialog.askstring("Editar", f"Nuevo {clave}:", initialvalue=str(curr))
            if nuevo is not None:
                lista_ref[int(item_id)][clave] = nuevo
                vals = list(tree.item(item_id, 'values'))
                vals[idx] = nuevo
                tree.item(item_id, values=vals)
                # Se ajusta el ancho tras edición
                self._ajustar_columnas(tree, self.cols_f2, lista_ref)

    def _popup_menu_giros(self, tree, item_id, lista_ref, clave, idx):
        top = tk.Toplevel(self.root)
        top.title("Giro")
        var = tk.StringVar(top)
        var.set(LISTA_CATEGORIAS[0])
        ttk.OptionMenu(top, var, LISTA_CATEGORIAS[0], *LISTA_CATEGORIAS).pack(padx=10, pady=10)
        def ok():
            sel = var.get()
            if sel == "OTRO": sel = simpledialog.askstring("Otro", "Giro:")
            if sel:
                lista_ref[int(item_id)][clave] = sel
                vals = list(tree.item(item_id, 'values'))
                vals[idx] = sel
                tree.item(item_id, values=vals)
                self._ajustar_columnas(tree, self.cols_f2, lista_ref)
            top.destroy()
        ttk.Button(top, text="OK", command=ok).pack()

    def ir_a_fase_3(self):
        # Se guardan los datos generales modificados
        self._guardar_datos_generales_ui_en_memoria(self.periodo_actual_f2, self.entries_datos_generales_f2)
        
        self.archivos_para_fase3 = []
        
        for periodo, struct in self.master_data.items():
            # Se guardan los archivos con giros modificados
            for tipo_cat in ["cat_ingresos", "cat_egresos"]:
                if struct[tipo_cat]:
                    base = struct["nombre_base_match"]
                    tipo_str = "INGRESOS" if "ingresos" in tipo_cat else "EGRESOS"
                    
                    # Se genera el nombre exacto del archivo de salida
                    nombre_final = f"{base}_{tipo_str}_MODIFICADO_CON_GIROS_MODIFICADO.json"
                    
                    ruta_out = ORQ_OUTPUT / nombre_final
                    with open(ruta_out, 'w', encoding='utf-8') as f:
                          json.dump(struct[tipo_cat], f, indent=4, ensure_ascii=False)
                    
                    self.archivos_para_fase3.append(ruta_out)
            
            # Se guardan los datos modificados
            if struct["datos_gen"]:
                base = struct["nombre_base_match"]
                
                # Se genera el nombre exacto del archivo de salida
                nombre_final_datos = f"{base}_DATOS_MODIFICADO_MODIFICADO.json"
                
                ruta_out_datos = ORQ_OUTPUT / nombre_final_datos
                with open(ruta_out_datos, 'w', encoding='utf-8') as f:
                    json.dump(struct["datos_gen"], f, indent=4, ensure_ascii=False)

        self.notebook.select(self.tab_fase3)

    # Se construye la pestaña de la fase 3
    def _construir_tab_fase3(self):
        f = ttk.Frame(self.tab_fase3)
        f.pack(fill='x', pady=10, padx=10)
        
        self.progress_f3 = ttk.Progressbar(f, mode='indeterminate')
        self.progress_f3.pack(side='bottom', fill='x', pady=5)

        ttk.Button(f, text="Iniciar Perfilado", command=self.ejecutar_fase_3).pack(side='left')
        self.lbl_f3 = ttk.Label(f, text="Esperando...")
        self.lbl_f3.pack(side='left', padx=10)
        
        # Se agrega el botón de guardado final
        ttk.Button(f, text="Guardar Final", command=self.guardar_fase_3).pack(side='right')

        frame_sel = ttk.LabelFrame(self.tab_fase3, text="Selección Periodo")
        frame_sel.pack(fill='x', padx=10, pady=5)
        ttk.Label(frame_sel, text="Periodo:").pack(side='left')
        self.combo_periodos_f3 = ttk.Combobox(frame_sel, state="readonly", width=50)
        self.combo_periodos_f3.pack(side='left', padx=5)
        self.combo_periodos_f3.bind("<<ComboboxSelected>>", self._cambiar_periodo_f3)

        self.cols_f3 = [
            "Nombre de la empresa del estado de cuenta", "Numero de cuenta del estado de cuenta",
            "Periodo del estado de cuenta", "Saldo inicial de la cuenta", "Saldo final de la cuenta",
            "Saldo promedio del periodo", "Cantidad total de depositos", "Cantidad total de retiros",
            "Giro de la empresa", "Top_3_Giros_Probables", "Justificacion_Auditoria"
        ]

        self.tree_f3 = ttk.Treeview(self.tab_fase3, columns=self.cols_f3, show='headings')
        vsb = ttk.Scrollbar(self.tab_fase3, orient="vertical", command=self.tree_f3.yview)
        hsb = ttk.Scrollbar(self.tab_fase3, orient="horizontal", command=self.tree_f3.xview)
        self.tree_f3.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.pack(side='right', fill='y')
        hsb.pack(side='bottom', fill='x')
        self.tree_f3.pack(fill='both', expand=True, padx=10, pady=10)

        for c in self.cols_f3: 
            self.tree_f3.heading(c, text=c)
            # Se deshabilita el estiramiento automático de columnas
            self.tree_f3.column(c, width=150, stretch=False)
        
        # Se vincula el evento de doble clic para edición
        self.tree_f3.bind("<Double-1>", self.editar_celda_fase3)

        self.frame_datos_gen_f3 = ttk.LabelFrame(self.tab_fase3, text="Datos Generales del Periodo (Final)")
        self.frame_datos_gen_f3.pack(side='bottom', fill='x', padx=10, pady=10)

    def ejecutar_fase_3(self):
        self.progress_f3.start(10)
        threading.Thread(target=self._logica_fase_3).start()

    def _logica_fase_3(self):
        self.lbl_f3.config(text="Procesando...")
        
        # Se limpia el directorio de entrada del modelo
        limpiar_directorio(MOD_INPUT) 
        
        # Se buscan archivos prioritarios
        archivos_datos = list(ORQ_OUTPUT.glob("*_DATOS_MODIFICADO_MODIFICADO.json"))
        
        # Se configura un mecanismo de respaldo si no existen los prioritarios
        if not archivos_datos: archivos_datos = list(ORQ_OUTPUT.glob("*_DATOS_MODIFICADO.json"))
        if not archivos_datos: archivos_datos = list(ORQ_OUTPUT.glob("*_DATOS.json"))
        
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        try:
            mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
            os.chdir(DIR_MODELOS)
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
            for datos_json in archivos_datos:
                print(f"Preparando archivos para: {datos_json.name}")
                
                # Se obtiene el nombre base del archivo
                nombre_original = datos_json.name
                base_limpia = re.sub(r'(_DATOS|_MODIFICADO|_CON_GIROS)*\.json$', '', nombre_original, flags=re.IGNORECASE)
                
                # Se definen los nombres estandarizados para el modelo
                target_datos = f"{base_limpia}_DATOS.json"
                target_ing = f"{base_limpia}_INGRESOS_CON_GIROS.json"
                target_egr = f"{base_limpia}_EGRESOS_CON_GIROS.json"
                
                # Se identifican los archivos fuente
                if "_DATOS_MODIFICADO_MODIFICADO.json" in nombre_original:
                    src_ing = ORQ_OUTPUT / f"{base_limpia}_INGRESOS_MODIFICADO_CON_GIROS_MODIFICADO.json"
                    src_egr = ORQ_OUTPUT / f"{base_limpia}_EGRESOS_MODIFICADO_CON_GIROS_MODIFICADO.json"
                else:
                    src_ing = ORQ_OUTPUT / f"{base_limpia}_INGRESOS_CON_GIROS.json"
                    src_egr = ORQ_OUTPUT / f"{base_limpia}_EGRESOS_CON_GIROS.json"

                # Se copian y renombran los archivos
                shutil.copy(datos_json, MOD_INPUT / target_datos)
                
                if src_ing.exists():
                    shutil.copy(src_ing, MOD_INPUT / target_ing)
                    print(f"  -> Ingresos detectado y normalizado a: {target_ing}")
                else:
                    posibles = list(ORQ_OUTPUT.glob(f"{base_limpia}*INGRESOS*.json"))
                    if posibles:
                        shutil.copy(posibles[0], MOD_INPUT / target_ing)
                        print(f"  -> (Aviso) Usando match aproximado para Ingresos: {posibles[0].name}")
                    else:
                        print(f"  -> ERROR: No se encuentra archivo de Ingresos para {base_limpia}")

                if src_egr.exists():
                    shutil.copy(src_egr, MOD_INPUT / target_egr)
                    print(f"  -> Egresos detectado y normalizado a: {target_egr}")
                else:
                    posibles = list(ORQ_OUTPUT.glob(f"{base_limpia}*EGRESOS*.json"))
                    if posibles:
                        shutil.copy(posibles[0], MOD_INPUT / target_egr)
                        print(f"  -> (Aviso) Usando match aproximado para Egresos: {posibles[0].name}")
                    else:
                         print(f"  -> ERROR: No se encuentra archivo de Egresos para {base_limpia}")

                # Se ejecuta el modelo de perfilado
                print(f"Ejecutando modelo sobre: {target_datos}")
                mod.procesar_perfilado_empresarial(llm, target_datos)
                
                # Se recuperan los resultados generados
                out_path = MOD_OUTPUT / target_datos 
                
                if out_path.exists():
                    # Se define el nombre final para la interfaz
                    dest_final = ORQ_OUTPUT / (datos_json.stem + "_PERFIL.json")
                    shutil.move(str(out_path), str(dest_final))
                    print(f"Perfilado Éxito: {dest_final.name}")
                    
                    with open(dest_final, 'r') as f: perfil = json.load(f)
                    for periodo, struct in self.master_data.items():
                        if struct["nombre_base_match"] in dest_final.name:
                            struct["perfil"] = perfil
                else:
                    print("Error: El modelo no generó el archivo de salida esperado.")
            
            del llm; gc.collect()

        except Exception as e:
            print(f"Error fase 3: {e}")
            import traceback
            traceback.print_exc()
        
        self.root.after(0, self._actualizar_ui_f3)

    def _actualizar_ui_f3(self):
        self.progress_f3.stop()
        self.lbl_f3.config(text="Finalizado.")
        periodos = list(self.master_data.keys())
        self.combo_periodos_f3['values'] = periodos
        if periodos:
            self.combo_periodos_f3.current(0)
            self._cambiar_periodo_f3(None)

    def _cambiar_periodo_f3(self, event):
        periodo = self.combo_periodos_f3.get()
        if not periodo or periodo not in self.master_data: return
        data = self.master_data[periodo]
        self.entries_datos_generales_f3 = {}
        self._construir_form_datos_generales(self.frame_datos_gen_f3, data["datos_gen"], self.entries_datos_generales_f3)
        
        self.tree_f3.delete(*self.tree_f3.get_children())
        d = data.get("perfil")
        if d:
            if isinstance(d, list): d = d[0]
            vals = []
            for col in self.cols_f3:
                vals.append(str(d.get(col, "")))
            self.tree_f3.insert("", "end", values=vals)
            # Se ajustan las columnas para la fase 3
            self._ajustar_columnas(self.tree_f3, self.cols_f3, [d])

    def editar_celda_fase3(self, event):
        # Se permite la edición en la fase 3
        sel = self.tree_f3.selection()
        if not sel: return
        
        col_id = self.tree_f3.identify_column(event.x)
        idx = int(col_id.replace('#', '')) - 1
        clave = self.cols_f3[idx]
        
        periodo = self.combo_periodos_f3.get()
        if not periodo: return
        
        data_ref = self.master_data[periodo]["perfil"]
        if isinstance(data_ref, list): data_ref = data_ref[0]
        
        curr = data_ref.get(clave, "")
        nuevo = simpledialog.askstring("Editar Fase 3", f"Nuevo {clave}:", initialvalue=str(curr))
        
        if nuevo is not None:
            data_ref[clave] = nuevo
            vals = list(self.tree_f3.item(sel[0], 'values'))
            vals[idx] = nuevo
            self.tree_f3.item(sel[0], values=vals)
            self._ajustar_columnas(self.tree_f3, self.cols_f3, [data_ref])

    def guardar_fase_3(self):
        # Se guarda el estado final de los perfiles con el nombre solicitado
        count = 0
        for periodo, struct in self.master_data.items():
            if struct.get("perfil"):
                base = struct["nombre_base_match"] 
                
                # Se busca si existe un perfil previo para usar como base del nombre
                candidatos = list(ORQ_OUTPUT.glob(f"*{base}*_PERFIL.json"))
                
                if candidatos:
                    origen = max(candidatos, key=lambda p: len(p.name))
                    # Se quita la extensión .json y se agrega _MODIFICADO.json
                    nombre_final = origen.stem + "_MODIFICADO.json"
                else:
                    # Se utiliza un mecanismo alternativo si no existe previo
                    nombre_final = f"{base}_DATOS_MODIFICADO_MODIFICADO_PERFIL_MODIFICADO.json"
                
                ruta_final = ORQ_OUTPUT / nombre_final
                
                with open(ruta_final, 'w', encoding='utf-8') as f:
                    json.dump(struct["perfil"], f, indent=4, ensure_ascii=False)
                count += 1
        
        if count > 0:
            messagebox.showinfo("Guardado", f"Se guardaron {count} perfiles finales correctamente.")
        else:
            messagebox.showwarning("Atención", "No hay datos de perfil para guardar.")

if __name__ == "__main__":
    root = tk.Tk()
    app = CategorizadorApp(root)
    root.mainloop()