import os
import sys
import json
import time
import shutil
import logging
import threading
import tkinter as tk
from tkinter import messagebox

# Carga dinámica de dependencias
try:
    import customtkinter as ctk
    import pystray
    from PIL import Image, ImageDraw, ImageTk
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError as e:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "Faltan dependencias",
        f"No se pudieron cargar las librerías necesarias: {e}\n\n"
        "Por favor, ejecuta el archivo 'CONFIGURAR.bat' para instalar todos los componentes automáticamente."
    )
    sys.exit(1)

# Directorios del sistema y logs
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "organizador.log")

# Configurar registro de logs interno
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# Diccionario de equivalencias de temas Español -> Inglés
THEME_ES_TO_EN = {
    "Sistema": "System",
    "Oscuro": "Dark",
    "Claro": "Light"
}
THEME_EN_TO_ES = {v: k for k, v in THEME_ES_TO_EN.items()}

# Diccionario de equivalencias de criterios para el usuario
CRITERIO_ES_TO_EN = {
    "Contiene": "contiene",
    "Empieza por": "empieza_con",
    "Termina en": "termina_con",
    "Autoclasificar": "autoclasificar"
}
CRITERIO_EN_TO_ES = {v: k for k, v in CRITERIO_ES_TO_EN.items()}

# Diccionario de equivalencias de acciones para el usuario
ACCION_ES_TO_EN = {
    "Mover": "mover",
    "Copiar": "copiar"
}
ACCION_EN_TO_ES = {v: k for k, v in ACCION_ES_TO_EN.items()}

def expand_path(path):
    """Expande las variables de entorno de Windows y retorna la ruta absoluta."""
    if not path:
        return ""
    return os.path.abspath(os.path.expandvars(path))

def parse_json_with_comments(filepath):
    """Lee el archivo config.json, tolera comentarios y corrige rutas mal formadas."""
    if not os.path.exists(filepath):
        example_path = os.path.join(os.path.dirname(filepath), "config.example.json")
        if os.path.exists(example_path):
            try:
                shutil.copy(example_path, filepath)
            except Exception:
                pass
        else:
            return {"carpeta_vigilada": "%USERPROFILE%\\Downloads", "tema_visual": "Oscuro", "reglas": []}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue
            cleaned.append(line)
            
        content = "".join(cleaned)
        
        # Corregir barras invertidas de Windows no escapadas en el JSON
        content = content.replace('\\\\', '__DOUBLE_BS__')
        content = content.replace('\\"', '__ESC_QUOTE__')
        content = content.replace('\\', '\\\\')
        content = content.replace('__DOUBLE_BS__', '\\\\')
        content = content.replace('__ESC_QUOTE__', '\\"')
        
        return json.loads(content)
    except Exception:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"carpeta_vigilada": "%USERPROFILE%\\Downloads", "tema_visual": "Oscuro", "reglas": []}

def save_config(filepath, config):
    """Guarda los cambios en el archivo config.json de manera limpia y formateada."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Error al escribir config.json: {e}")
        return False

def wait_for_file_ready(filepath, timeout=10):
    """Espera a que un archivo termine de descargarse o copiarse (liberando su lock)."""
    if not os.path.exists(filepath):
        return False
    
    start_time = time.time()
    last_size = -1
    
    while time.time() - start_time < timeout:
        try:
            current_size = os.path.getsize(filepath)
            if current_size == last_size:
                with open(filepath, 'ab') as f:
                    pass
                return True
            else:
                last_size = current_size
        except (IOError, OSError):
            pass
        time.sleep(0.5)
    return False

def get_unique_filename(filepath):
    """Evita sobrescribir archivos agregando un contador al nombre."""
    if not os.path.exists(filepath):
        return filepath
    dirname, filename = os.path.split(filepath)
    name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_filename = f"{name} ({counter}){ext}"
        new_filepath = os.path.join(dirname, new_filename)
        if not os.path.exists(new_filepath):
            return new_filepath
        counter += 1

def criterion_matches(filename, criterio, texto):
    """Valida si el nombre cumple las reglas (insensible a mayúsculas)."""
    filename_lower = filename.lower()
    texto_lower = texto.lower()
    
    if criterio == "empieza_con":
        return filename_lower.startswith(texto_lower)
    elif criterio == "termina_con":
        return filename_lower.endswith(texto_lower)
    elif criterio == "contiene":
        return texto_lower in filename_lower
    return False


class OrganizerHandler(FileSystemEventHandler):
    """Handler del Watchdog que carga las reglas al vuelo al detectar cambios en el disco."""
    def __init__(self, config_file_path, log_callback):
        super().__init__()
        self.config_file_path = config_file_path
        self.log = log_callback
        self.config = {}
        self.processed_files = {}  # Cache de archivos procesados con su mtime
        self.load_config()

    def load_config(self):
        self.config = parse_json_with_comments(self.config_file_path)

    def process_file(self, filepath):
        self.load_config()
        if not os.path.exists(filepath):
            return
        if os.path.isdir(filepath):
            return

        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            return

        # Evitar procesar el mismo archivo si ya se le aplicó una regla en su estado actual
        if self.processed_files.get(filepath) == mtime:
            return

        filename = os.path.basename(filepath)
        temp_extensions = ('.tmp', '.crdownload', '.part', '.lock', '.download', '.tmp_')
        if filename.lower().endswith(temp_extensions) or filename.startswith('~$'):
            return

        self.log(f"Detectado archivo para analizar: {filename}")

        if not wait_for_file_ready(filepath):
            self.log(f"Archivo bloqueado/incompleto, se omite: {filename}")
            return

        reglas = self.config.get("reglas", [])
        config_changed = False

        for regla in reglas:
            criterio = regla.get("criterio")
            texto = regla.get("texto", "")
            accion = regla.get("accion")
            carpeta_destino = regla.get("carpeta_destino")
            crear_subcarpeta = regla.get("crear_subcarpeta", False)

            if not criterio or not texto or not accion or not carpeta_destino:
                continue

            # Modo Autoclasificar Dinámico
            if criterio == "autoclasificar":
                if texto in filename:
                    prefix = filename.split(texto, 1)[0].strip()
                    if not prefix:
                        continue
                    
                    base_dest = expand_path(carpeta_destino)
                    destino_dir = os.path.join(base_dest, prefix)
                    
                    try:
                        os.makedirs(destino_dir, exist_ok=True)
                    except Exception as e:
                        self.log(f"Error al crear carpeta autoclasificada {destino_dir}: {e}")
                        continue
                    
                    dest_filepath = os.path.join(destino_dir, filename)
                    dest_filepath = get_unique_filename(dest_filepath)
                    
                    try:
                        if accion == "mover":
                            shutil.move(filepath, dest_filepath)
                            self.log(f"Autoclasificado (Mover): '{filename}' ➔ '{dest_filepath}'")
                        elif accion == "copiar":
                            shutil.copy2(filepath, dest_filepath)
                            self.log(f"Autoclasificado (Copiar): '{filename}' ➔ '{dest_filepath}'")
                        
                        self.processed_files[filepath] = mtime
                        
                        # Generar nueva regla estática aprendida
                        nuevo_destino_rel = os.path.join(carpeta_destino, prefix)
                        nuevo_texto_regla = prefix + texto
                        
                        existe_regla = False
                        for r in reglas:
                            if r.get("criterio") == "empieza_con" and r.get("texto") == nuevo_texto_regla:
                                existe_regla = True
                                break
                        
                        if not existe_regla:
                            nueva_regla = {
                                "criterio": "empieza_con",
                                "texto": nuevo_texto_regla,
                                "accion": accion,
                                "carpeta_destino": nuevo_destino_rel,
                                "crear_subcarpeta": False
                            }
                            reglas.insert(0, nueva_regla)
                            config_changed = True
                        break
                    except Exception as e:
                        self.log(f"Error en autoclasificación de '{filename}': {e}")
                continue

            # Reglas Estándar
            if criterion_matches(filename, criterio, texto):
                destino_dir = expand_path(carpeta_destino)
                
                if crear_subcarpeta:
                    folder_name = texto.replace(".", "") if texto.startswith(".") else texto
                    destino_dir = os.path.join(destino_dir, folder_name)

                try:
                    os.makedirs(destino_dir, exist_ok=True)
                except Exception as e:
                    self.log(f"Error al crear destino {destino_dir}: {e}")
                    continue

                dest_filepath = os.path.join(destino_dir, filename)
                dest_filepath = get_unique_filename(dest_filepath)

                try:
                    if accion == "mover":
                        shutil.move(filepath, dest_filepath)
                        self.log(f"Movido: '{filename}' ➔ '{dest_filepath}'")
                        self.processed_files[filepath] = mtime
                        break
                    elif accion == "copiar":
                        shutil.copy2(filepath, dest_filepath)
                        self.log(f"Copiado: '{filename}' ➔ '{dest_filepath}'")
                        self.processed_files[filepath] = mtime
                        break
                except Exception as e:
                    self.log(f"Error al procesar '{filename}' ({accion}): {e}")

        if config_changed:
            self.config["reglas"] = reglas
            save_config(self.config_file_path, self.config)

    def on_created(self, event):
        if not event.is_directory:
            time.sleep(0.5)
            self.process_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            time.sleep(0.5)
            self.process_file(event.dest_path)


class OrganizadorApp:
    def __init__(self, start_minimized=False):
        self.config_path = os.path.join(SCRIPT_DIR, "config.json")
        self.log_file = LOG_FILE
        self.watcher_lock = threading.Lock()
        self.observer = None
        self.tray_icon = None
        self.editing_rule_index = -1
        
        # Cargar configuración
        self.config = parse_json_with_comments(self.config_path)
        
        # Configuración de Ventana
        self.window = ctk.CTk()
        self.window.title("Organizador de Archivos")
        self.window.geometry("980x660")
        self.window.resizable(True, True)
        self.window.minsize(450, 600)
        
        # Configurar icono de la aplicación (logo.ico)
        self.logo_ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.ico")
        self.logo_png_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        
        # Generar logo.ico si no existe pero logo.png sí
        if os.path.exists(self.logo_png_path) and not os.path.exists(self.logo_ico_path):
            try:
                img = Image.open(self.logo_png_path)
                img.save(self.logo_ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
            except Exception as e:
                self.log(f"No se pudo convertir logo.png a logo.ico: {e}")
                
        if os.path.exists(self.logo_ico_path):
            try:
                self.window.iconbitmap(self.logo_ico_path)
            except Exception as e:
                self.log(f"No se pudo cargar el logo de la ventana: {e}")
        
        # Enlaces de eventos para soporte responsive y pantalla completa
        self.window.bind("<F11>", self.toggle_fullscreen)
        self.window.bind("<Configure>", self.on_window_configure)
        
        # Estética de Diseño Dinámica (Soporte Claro / Oscuro con tuplas de color)
        theme_es = self.config.get("tema_visual", "Oscuro")
        theme_en = THEME_ES_TO_EN.get(theme_es, "Dark")
        ctk.set_appearance_mode(theme_en)
        ctk.set_default_color_theme("blue")
        
        # Interceptar cierre de ventana (X)
        self.window.protocol("WM_DELETE_WINDOW", self.on_close_window)
        
        # Layout de Rejilla Principal
        self.window.grid_rowconfigure(0, weight=1)
        self.window.grid_columnconfigure(1, weight=1)
        
        # --- Sidebar Izquierdo ---
        self.sidebar_frame = ctk.CTkFrame(self.window, width=220, corner_radius=0, fg_color=("#F3F3F5", "#18181A"))
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)
        
        # Título en Sidebar
        self.sidebar_title = ctk.CTkLabel(
            self.sidebar_frame,
            text="📂 Organizador",
            font=("Segoe UI", 20, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.sidebar_title.grid(row=0, column=0, padx=20, pady=(25, 20), sticky="w")
        
        # Botones de navegación del Sidebar
        self.btn_vigilancia = ctk.CTkButton(
            self.sidebar_frame,
            text="  🔎 Vigilancia y Estado",
            font=("Segoe UI", 13, "bold"),
            anchor="w",
            height=40,
            corner_radius=8,
            fg_color="transparent",
            text_color=("#4E4E52", "#C0C0C2"),
            hover_color=("#E5E5EA", "#2B2B2D"),
            command=lambda: self.select_frame_by_name("vigilancia")
        )
        self.btn_vigilancia.grid(row=1, column=0, padx=12, pady=5, sticky="ew")
        
        self.btn_reglas = ctk.CTkButton(
            self.sidebar_frame,
            text="  ⚡ Reglas Activas",
            font=("Segoe UI", 13, "bold"),
            anchor="w",
            height=40,
            corner_radius=8,
            fg_color="transparent",
            text_color=("#4E4E52", "#C0C0C2"),
            hover_color=("#E5E5EA", "#2B2B2D"),
            command=lambda: self.select_frame_by_name("reglas")
        )
        self.btn_reglas.grid(row=2, column=0, padx=12, pady=5, sticky="ew")
        
        self.btn_ajustes = ctk.CTkButton(
            self.sidebar_frame,
            text="  ⚙️ Ajustes y Sistema",
            font=("Segoe UI", 13, "bold"),
            anchor="w",
            height=40,
            corner_radius=8,
            fg_color="transparent",
            text_color=("#4E4E52", "#C0C0C2"),
            hover_color=("#E5E5EA", "#2B2B2D"),
            command=lambda: self.select_frame_by_name("ajustes")
        )
        self.btn_ajustes.grid(row=3, column=0, padx=12, pady=5, sticky="ew")
        
        # Badge de Estado en el Sidebar
        self.sidebar_status_title = ctk.CTkLabel(
            self.sidebar_frame,
            text="ESTADO DEL MOTOR:",
            font=("Segoe UI", 9, "bold"),
            text_color=("#8E8E93", "#6B6B6D")
        )
        self.sidebar_status_title.grid(row=5, column=0, padx=20, pady=(15, 0), sticky="w")
        
        self.sidebar_status_badge = ctk.CTkLabel(
            self.sidebar_frame,
            text="● CARGANDO...",
            font=("Segoe UI", 11, "bold"),
            text_color=("#707070", "#A0A0A0"),
            fg_color=("#E5E5EA", "#252528"),
            corner_radius=8,
            height=30
        )
        self.sidebar_status_badge.grid(row=6, column=0, padx=15, pady=(5, 25), sticky="ew")
        
        # --- Contenedor Derecho (Frames Dinámicos) ---
        self.content_frame = ctk.CTkFrame(self.window, corner_radius=0, fg_color=("#F9F9FB", "#202022"))
        self.content_frame.grid(row=0, column=1, sticky="nsew")
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)
        
        self.setup_vigilancia_frame()
        self.setup_reglas_frame()
        self.setup_ajustes_frame()
        
        # Seleccionar por defecto la pestaña de Vigilancia
        self.select_frame_by_name("vigilancia")
        
        # Cargar valores actuales en inputs
        self.load_gui_data()
        
        # Preparar Bandeja del Sistema
        self.setup_tray()
        
        # Iniciar servicio Watchdog
        self.start_watcher_internal()
        
        # Mostrar o Minimizar según argumento
        if start_minimized:
            self.window.withdraw()
        else:
            self.window.deiconify()

    # --- Gestión del Sistema de Navegación ---
    def select_frame_by_name(self, name):
        # Actualizar estilo visual de los botones del Sidebar
        self.btn_vigilancia.configure(
            fg_color="#6C5CE7" if name == "vigilancia" else "transparent",
            text_color="#FFFFFF" if name == "vigilancia" else ("#4E4E52", "#C0C0C2")
        )
        self.btn_reglas.configure(
            fg_color="#6C5CE7" if name == "reglas" else "transparent",
            text_color="#FFFFFF" if name == "reglas" else ("#4E4E52", "#C0C0C2")
        )
        self.btn_ajustes.configure(
            fg_color="#6C5CE7" if name == "ajustes" else "transparent",
            text_color="#FFFFFF" if name == "ajustes" else ("#4E4E52", "#C0C0C2")
        )
        
        # Ocultar / Mostrar Frames correspondientes
        if name == "vigilancia":
            self.frame_vigilancia.grid(row=0, column=0, sticky="nsew", padx=30, pady=25)
        else:
            self.frame_vigilancia.grid_forget()
            
        if name == "reglas":
            self.frame_reglas.grid(row=0, column=0, sticky="nsew", padx=30, pady=25)
        else:
            self.frame_reglas.grid_forget()
            
        if name == "ajustes":
            self.frame_ajustes.grid(row=0, column=0, sticky="nsew", padx=30, pady=25)
        else:
            self.frame_ajustes.grid_forget()

    # --- Creación de los paneles de contenido ---
    def setup_vigilancia_frame(self):
        self.frame_vigilancia = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        
        # Cabecera de Página
        lbl_title = ctk.CTkLabel(
            self.frame_vigilancia,
            text="Control del Organizador",
            font=("Segoe UI", 26, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_title.pack(anchor="w", pady=(10, 5))
        
        lbl_subtitle = ctk.CTkLabel(
            self.frame_vigilancia,
            text="Administra el estado general y el directorio de origen de los archivos.",
            font=("Segoe UI", 13),
            text_color=("#707070", "#8E8E93")
        )
        lbl_subtitle.pack(anchor="w", pady=(0, 20))
        
        # Tarjeta Visual de Estado
        card_estado = ctk.CTkFrame(self.frame_vigilancia, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"), corner_radius=12)
        card_estado.pack(fill="x", pady=10)
        
        self.lbl_status_desc = ctk.CTkLabel(
            card_estado,
            text="Estado de la Automatización",
            font=("Segoe UI", 11, "bold"),
            text_color=("#8E8E93", "#9F9F9F")
        )
        self.lbl_status_desc.pack(anchor="w", padx=20, pady=(15, 2))
        
        self.status_text_large = ctk.CTkLabel(
            card_estado,
            text="ANALIZANDO...",
            font=("Segoe UI", 24, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.status_text_large.pack(anchor="w", padx=20, pady=(0, 15))
        
        # Tarjeta de Carpeta Vigilada
        card_carpeta = ctk.CTkFrame(self.frame_vigilancia, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"), corner_radius=12)
        card_carpeta.pack(fill="x", pady=15)
        
        lbl_carpeta_title = ctk.CTkLabel(
            card_carpeta,
            text="Carpeta de Origen (Vigilada)",
            font=("Segoe UI", 14, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_carpeta_title.pack(anchor="w", padx=20, pady=(18, 5))
        
        lbl_carpeta_desc = ctk.CTkLabel(
            card_carpeta,
            text="Todos los archivos que caigan en este directorio serán organizados automáticamente según tus reglas.",
            font=("Segoe UI", 11),
            text_color=("#707070", "#8E8E93")
        )
        lbl_carpeta_desc.pack(anchor="w", padx=20, pady=(0, 12))
        
        dir_controls = ctk.CTkFrame(card_carpeta, fg_color="transparent")
        dir_controls.pack(fill="x", padx=20, pady=(0, 20))
        
        self.watch_entry = ctk.CTkEntry(
            dir_controls,
            placeholder_text="Selecciona un directorio usando el botón Examinar",
            height=38,
            fg_color=("#F3F3F5", "#1E1E20"),
            border_color=("#D1D1D6", "#38383B"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.watch_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.btn_browse = ctk.CTkButton(
            dir_controls,
            text="Examinar...",
            width=100,
            height=38,
            fg_color=("#E5E5EA", "#3A3A3D"),
            hover_color=("#D1D1D6", "#4E4E52"),
            text_color=("#1A1A1A", "#FFFFFF"),
            font=("Segoe UI", 12, "bold"),
            command=self.browse_watch_folder
        )
        self.btn_browse.pack(side="left", padx=(0, 10))
        
        self.btn_save_dir = ctk.CTkButton(
            dir_controls,
            text="Guardar Cambios",
            width=130,
            height=38,
            fg_color="#6C5CE7",
            hover_color="#5B4CC4",
            text_color="#FFFFFF",
            font=("Segoe UI", 12, "bold"),
            command=self.update_watch_folder
        )
        self.btn_save_dir.pack(side="left")
        
        # Accesos Rápidos
        quick_frame = ctk.CTkFrame(self.frame_vigilancia, fg_color="transparent")
        quick_frame.pack(fill="x", pady=15)
        
        self.toggle_watch_btn = ctk.CTkButton(
            quick_frame,
            text="Pausar Vigilancia",
            height=42,
            font=("Segoe UI", 13, "bold"),
            text_color="#FFFFFF",
            command=self.toggle_watcher
        )
        self.toggle_watch_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.btn_go_rules = ctk.CTkButton(
            quick_frame,
            text="Ver mis Reglas de Organización",
            height=42,
            fg_color=("#E5E5EA", "#2B2B2D"),
            hover_color=("#D1D1D6", "#3A3A3D"),
            text_color=("#1A1A1A", "#FFFFFF"),
            font=("Segoe UI", 13, "bold"),
            command=lambda: self.select_frame_by_name("reglas")
        )
        self.btn_go_rules.pack(side="left", fill="x", expand=True)

    def setup_reglas_frame(self):
        self.frame_reglas = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.frame_reglas.grid_rowconfigure(2, weight=1)
        self.frame_reglas.grid_columnconfigure(0, weight=1)
        
        # Cabecera
        header_frame = ctk.CTkFrame(self.frame_reglas, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", pady=(10, 15))
        
        lbl_title = ctk.CTkLabel(
            header_frame,
            text="Reglas de Organización",
            font=("Segoe UI", 26, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_title.pack(anchor="w")
        
        lbl_sub = ctk.CTkLabel(
            header_frame,
            text="Define criterios fáciles y carpetas de destino para clasificar archivos.",
            font=("Segoe UI", 13),
            text_color=("#707070", "#8E8E93")
        )
        lbl_sub.pack(anchor="w")
        
        # Formulario de Entrada
        self.form_card = ctk.CTkFrame(self.frame_reglas, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"), corner_radius=12)
        self.form_card.grid(row=1, column=0, sticky="ew", pady=(0, 15))
        
        # Grid Interno del Formulario
        self.form_card.grid_columnconfigure(0, weight=1)
        self.form_card.grid_columnconfigure(1, weight=1)
        self.form_card.grid_columnconfigure(2, weight=1)
        
        # Criterio
        self.lbl_crit = ctk.CTkLabel(self.form_card, text="Si el nombre del archivo:", font=("Segoe UI", 11, "bold"), text_color=("#555555", "#A0A0A2"))
        self.lbl_crit.grid(row=0, column=0, padx=15, pady=(15, 3), sticky="w")
        
        self.criterio_var = ctk.StringVar(value="Contiene")
        self.crit_menu = ctk.CTkOptionMenu(
            self.form_card,
            variable=self.criterio_var,
            values=["Contiene", "Empieza por", "Termina en", "Autoclasificar"],
            height=36,
            fg_color=("#F3F3F5", "#1E1E20"),
            button_color=("#E5E5EA", "#2D2D30"),
            button_hover_color=("#D1D1D6", "#3A3A3D"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.crit_menu.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="ew")
        
        # Palabra Clave
        self.lbl_texto = ctk.CTkLabel(self.form_card, text="Palabra clave / Extensión:", font=("Segoe UI", 11, "bold"), text_color=("#555555", "#A0A0A2"))
        self.lbl_texto.grid(row=0, column=1, padx=10, pady=(15, 3), sticky="w")
        
        self.texto_entry = ctk.CTkEntry(
            self.form_card,
            placeholder_text="Ej: facturas, .pdf, -",
            height=36,
            fg_color=("#F3F3F5", "#1E1E20"),
            border_color=("#D1D1D6", "#38383B"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.texto_entry.grid(row=1, column=1, padx=10, pady=(0, 15), sticky="ew")
        
        # Acción
        self.lbl_accion = ctk.CTkLabel(self.form_card, text="Acción:", font=("Segoe UI", 11, "bold"), text_color=("#555555", "#A0A0A2"))
        self.lbl_accion.grid(row=0, column=2, padx=15, pady=(15, 3), sticky="w")
        
        self.accion_var = ctk.StringVar(value="Mover")
        self.accion_menu = ctk.CTkOptionMenu(
            self.form_card,
            variable=self.accion_var,
            values=["Mover", "Copiar"],
            height=36,
            fg_color=("#F3F3F5", "#1E1E20"),
            button_color=("#E5E5EA", "#2D2D30"),
            button_hover_color=("#D1D1D6", "#3A3A3D"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.accion_menu.grid(row=1, column=2, padx=15, pady=(0, 15), sticky="ew")
        
        # Carpeta Destino
        self.lbl_dest = ctk.CTkLabel(self.form_card, text="Hacia la carpeta de destino:", font=("Segoe UI", 11, "bold"), text_color=("#555555", "#A0A0A2"))
        self.lbl_dest.grid(row=2, column=0, padx=15, pady=(0, 3), sticky="w")
        
        self.dest_controls = ctk.CTkFrame(self.form_card, fg_color="transparent")
        self.dest_controls.grid(row=3, column=0, columnspan=3, padx=15, pady=(0, 15), sticky="ew")
        
        self.destino_entry = ctk.CTkEntry(
            self.dest_controls,
            placeholder_text="Ej: C:\\Usuarios\\Escritorio\\Documentos",
            height=36,
            fg_color=("#F3F3F5", "#1E1E20"),
            border_color=("#D1D1D6", "#38383B"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.destino_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        btn_browse_dest = ctk.CTkButton(
            self.dest_controls,
            text="Examinar...",
            width=90,
            height=36,
            fg_color=("#E5E5EA", "#3A3A3D"),
            hover_color=("#D1D1D6", "#4E4E52"),
            text_color=("#1A1A1A", "#FFFFFF"),
            font=("Segoe UI", 11, "bold"),
            command=self.browse_destination_folder
        )
        btn_browse_dest.pack(side="left", padx=(0, 10))
        
        # Checkbox Crear Subcarpeta
        self.subcarpeta_var = ctk.BooleanVar(value=True)
        self.subcarpeta_cb = ctk.CTkCheckBox(
            self.form_card,
            text="Crear carpeta ordenada (usa la palabra clave como nombre)",
            variable=self.subcarpeta_var,
            font=("Segoe UI", 12),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        self.subcarpeta_cb.grid(row=4, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")
        
        # Fila de Botones del formulario
        self.form_actions = ctk.CTkFrame(self.form_card, fg_color="transparent")
        self.form_actions.grid(row=4, column=1, columnspan=2, padx=15, pady=(0, 15), sticky="e")
        
        self.cancel_edit_btn = ctk.CTkButton(
            self.form_actions,
            text="Cancelar Edición",
            width=120,
            height=36,
            fg_color="#FF9F43",
            hover_color="#EE5253",
            text_color="#18181A",
            font=("Segoe UI", 12, "bold"),
            command=self.cancel_edit
        )
        # Se esconde por defecto
        self.cancel_edit_btn.pack_forget()
        
        self.add_rule_btn = ctk.CTkButton(
            self.form_actions,
            text="Añadir Regla",
            width=120,
            height=36,
            fg_color="#10AC84",
            hover_color="#0F9B75",
            text_color="#FFFFFF",
            font=("Segoe UI", 12, "bold"),
            command=self.save_or_add_rule
        )
        self.add_rule_btn.pack(side="right", padx=(10, 0))

        # --- SECCIÓN: PLANTILLAS RÁPIDAS (ACCESIBILIDAD) ---
        self.lbl_templates = ctk.CTkLabel(self.form_card, text="Plantillas de un clic para organizar extensiones comunes:", font=("Segoe UI", 10, "bold"), text_color=("#777777", "#8E8E93"))
        self.lbl_templates.grid(row=5, column=0, columnspan=3, padx=15, pady=(0, 5), sticky="w")

        self.templates_frame = ctk.CTkFrame(self.form_card, fg_color="transparent")
        self.templates_frame.grid(row=6, column=0, columnspan=3, padx=15, pady=(0, 15), sticky="ew")

        btn_t_pdf = ctk.CTkButton(
            self.templates_frame,
            text="📄 PDFs (.pdf)",
            height=28,
            fg_color=("#E5E5EA", "#323236"),
            hover_color=("#D1D1D6", "#45454A"),
            text_color=("#333333", "#E0E0E0"),
            font=("Segoe UI", 11),
            command=lambda: self.apply_quick_template("pdf")
        )
        btn_t_pdf.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_t_img = ctk.CTkButton(
            self.templates_frame,
            text="🖼️ Imágenes (.png)",
            height=28,
            fg_color=("#E5E5EA", "#323236"),
            hover_color=("#D1D1D6", "#45454A"),
            text_color=("#333333", "#E0E0E0"),
            font=("Segoe UI", 11),
            command=lambda: self.apply_quick_template("imagenes")
        )
        btn_t_img.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_t_vid = ctk.CTkButton(
            self.templates_frame,
            text="🎬 Videos (.mp4)",
            height=28,
            fg_color=("#E5E5EA", "#323236"),
            hover_color=("#D1D1D6", "#45454A"),
            text_color=("#333333", "#E0E0E0"),
            font=("Segoe UI", 11),
            command=lambda: self.apply_quick_template("videos")
        )
        btn_t_vid.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_t_zip = ctk.CTkButton(
            self.templates_frame,
            text="📦 Comprimidos (.zip)",
            height=28,
            fg_color=("#E5E5EA", "#323236"),
            hover_color=("#D1D1D6", "#45454A"),
            text_color=("#333333", "#E0E0E0"),
            font=("Segoe UI", 11),
            command=lambda: self.apply_quick_template("comprimidos")
        )
        btn_t_zip.pack(side="left", fill="x", expand=True)
        
        # Listado de Reglas Activas
        list_card = ctk.CTkFrame(self.frame_reglas, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"), corner_radius=12)
        list_card.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        list_card.grid_rowconfigure(1, weight=1)
        list_card.grid_columnconfigure(0, weight=1)
        
        lbl_list_title = ctk.CTkLabel(
            list_card,
            text="Listado de Reglas de Distribución",
            font=("Segoe UI", 14, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_list_title.grid(row=0, column=0, padx=20, pady=(15, 8), sticky="w")
        
        self.rules_scrollable_frame = ctk.CTkScrollableFrame(
            list_card,
            fg_color=("#F3F3F5", "#1E1E20"),
            corner_radius=8
        )
        self.rules_scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))

    def setup_ajustes_frame(self):
        self.frame_ajustes = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        
        # Cabecera
        lbl_title = ctk.CTkLabel(
            self.frame_ajustes,
            text="Ajustes de la Aplicación",
            font=("Segoe UI", 26, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_title.pack(anchor="w", pady=(10, 5))
        
        lbl_sub = ctk.CTkLabel(
            self.frame_ajustes,
            text="Gestione el comportamiento visual del programa y el mantenimiento de Windows.",
            font=("Segoe UI", 13),
            text_color=("#707070", "#8E8E93")
        )
        lbl_sub.pack(anchor="w", pady=(0, 20))
        
        # Tarjeta de Apariencia
        card_apariencia = ctk.CTkFrame(self.frame_ajustes, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"), corner_radius=12)
        card_apariencia.pack(fill="x", pady=10)
        
        lbl_tema = ctk.CTkLabel(
            card_apariencia,
            text="Estilo Visual del Panel",
            font=("Segoe UI", 14, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_tema.pack(anchor="w", padx=20, pady=(15, 2))
        
        lbl_tema_desc = ctk.CTkLabel(
            card_apariencia,
            text="Cambia entre el modo claro u oscuro.",
            font=("Segoe UI", 11),
            text_color=("#707070", "#8E8E93")
        )
        lbl_tema_desc.pack(anchor="w", padx=20, pady=(0, 10))
        
        theme_controls = ctk.CTkFrame(card_apariencia, fg_color="transparent")
        theme_controls.pack(fill="x", padx=20, pady=(0, 20))
        
        self.theme_var = ctk.StringVar(value="Oscuro")
        self.theme_menu = ctk.CTkOptionMenu(
            theme_controls,
            variable=self.theme_var,
            values=["Sistema", "Oscuro", "Claro"],
            height=36,
            fg_color=("#F3F3F5", "#1E1E20"),
            button_color=("#E5E5EA", "#2D2D30"),
            button_hover_color=("#D1D1D6", "#3A3A3D"),
            text_color=("#1A1A1A", "#FFFFFF"),
            command=self.change_theme
        )
        self.theme_menu.pack(side="left", padx=(0, 15))
        
        # Tarjeta de Administración
        card_admin = ctk.CTkFrame(self.frame_ajustes, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"), corner_radius=12)
        card_admin.pack(fill="x", pady=10)
        
        lbl_admin_title = ctk.CTkLabel(
            card_admin,
            text="Administración y Mantenimiento",
            font=("Segoe UI", 14, "bold"),
            text_color=("#1A1A1A", "#FFFFFF")
        )
        lbl_admin_title.pack(anchor="w", padx=20, pady=(15, 2))
        
        lbl_admin_desc = ctk.CTkLabel(
            card_admin,
            text="Funciones para ver registros locales, carpetas o detener el arranque automático.",
            font=("Segoe UI", 11),
            text_color=("#707070", "#8E8E93")
        )
        lbl_admin_desc.pack(anchor="w", padx=20, pady=(0, 15))
        
        self.admin_btns = ctk.CTkFrame(card_admin, fg_color="transparent")
        self.admin_btns.pack(fill="x", padx=20, pady=(0, 20))
        
        self.btn_open_app = ctk.CTkButton(
            self.admin_btns,
            text="📁 Directorio Local",
            height=38,
            fg_color=("#E5E5EA", "#3A3A3D"),
            hover_color=("#D1D1D6", "#4E4E52"),
            text_color=("#1A1A1A", "#FFFFFF"),
            font=("Segoe UI", 12, "bold"),
            command=self.open_app_directory
        )
        self.btn_open_app.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.btn_open_log = ctk.CTkButton(
            self.admin_btns,
            text="📝 Ver Log de Eventos",
            height=38,
            fg_color=("#E5E5EA", "#3A3A3D"),
            hover_color=("#D1D1D6", "#4E4E52"),
            text_color=("#1A1A1A", "#FFFFFF"),
            font=("Segoe UI", 12, "bold"),
            command=self.open_log_in_notepad
        )
        self.btn_open_log.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.btn_uninstall = ctk.CTkButton(
            self.admin_btns,
            text="🗑️ Desinstalar",
            height=38,
            fg_color="#D63031",
            hover_color="#C22021",
            text_color="#FFFFFF",
            font=("Segoe UI", 12, "bold"),
            command=self.uninstall_app
        )
        self.btn_uninstall.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.btn_exit = ctk.CTkButton(
            self.admin_btns,
            text="❌ Apagar Aplicación",
            height=38,
            fg_color=("#E5E5EA", "#2B2B2D"),
            hover_color=("#D1D1D6", "#3A3A3D"),
            text_color=("#1A1A1A", "#FFFFFF"),
            font=("Segoe UI", 12, "bold"),
            command=self.quit_app
        )
        self.btn_exit.pack(side="left", fill="x", expand=True)

        # Créditos inferiores
        credito_lbl = ctk.CTkLabel(
            self.frame_ajustes,
            text="Organizador de Archivos Inteligente - Versión 2.2.0\n"
                 "Diseñado con CustomTkinter para soporte fluido claro/oscuro.",
            font=("Segoe UI", 11, "italic"),
            text_color="#636E72"
        )
        credito_lbl.pack(pady=20)

    def toggle_fullscreen(self, event=None):
        """Alterna el estado de pantalla completa usando F11."""
        state = not self.window.attributes("-fullscreen")
        self.window.attributes("-fullscreen", state)
        return "break"

    def on_window_configure(self, event):
        """Filtra y maneja los eventos de redimensión aplicados únicamente a la ventana principal."""
        if event.widget == self.window:
            self.update_responsive_layout()

    def update_responsive_layout(self):
        """Adapta la interfaz dinámicamente si la ventana está orientada de forma vertical u horizontal."""
        if not hasattr(self, 'window') or not self.window.winfo_exists():
            return
            
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        
        if width <= 100 or height <= 100:
            return
            
        is_vertical = width < 780 or width < height
        
        if hasattr(self, '_last_is_vertical') and self._last_is_vertical == is_vertical:
            return
            
        self._last_is_vertical = is_vertical
        
        if is_vertical:
            # === DISEÑO VERTICAL (Header Superior) ===
            self.window.grid_columnconfigure(0, weight=1)
            self.window.grid_columnconfigure(1, weight=0)
            self.window.grid_rowconfigure(0, weight=0)
            self.window.grid_rowconfigure(1, weight=1)
            
            self.sidebar_frame.grid(row=0, column=0, sticky="ew")
            self.content_frame.grid(row=1, column=0, sticky="nsew")
            
            # Sidebar horizontal
            self.sidebar_frame.grid_rowconfigure(0, weight=0)
            self.sidebar_frame.grid_rowconfigure(1, weight=0)
            self.sidebar_frame.grid_rowconfigure(2, weight=0)
            self.sidebar_frame.grid_rowconfigure(3, weight=0)
            self.sidebar_frame.grid_rowconfigure(4, weight=0)
            self.sidebar_frame.grid_rowconfigure(5, weight=0)
            self.sidebar_frame.grid_rowconfigure(6, weight=0)
            
            self.sidebar_frame.grid_columnconfigure(0, weight=1)
            self.sidebar_frame.grid_columnconfigure(1, weight=1)
            self.sidebar_frame.grid_columnconfigure(2, weight=1)
            self.sidebar_frame.grid_columnconfigure(3, weight=1)
            self.sidebar_frame.grid_columnconfigure(4, weight=1)
            
            self.sidebar_title.grid(row=0, column=0, padx=15, pady=10, sticky="w")
            self.btn_vigilancia.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
            self.btn_reglas.grid(row=0, column=2, padx=5, pady=10, sticky="ew")
            self.btn_ajustes.grid(row=0, column=3, padx=5, pady=10, sticky="ew")
            
            self.sidebar_status_title.grid_forget()
            self.sidebar_status_badge.grid(row=0, column=4, padx=15, pady=10, sticky="e")
            self.sidebar_status_badge.configure(width=120)
            
            # Formulario de reglas (Fila vertical)
            self.lbl_crit.grid_forget()
            self.crit_menu.grid_forget()
            self.lbl_texto.grid_forget()
            self.texto_entry.grid_forget()
            self.lbl_accion.grid_forget()
            self.accion_menu.grid_forget()
            self.lbl_dest.grid_forget()
            self.dest_controls.grid_forget()
            self.subcarpeta_cb.grid_forget()
            self.form_actions.grid_forget()
            self.lbl_templates.grid_forget()
            self.templates_frame.grid_forget()
            
            self.form_card.grid_columnconfigure(0, weight=1)
            self.form_card.grid_columnconfigure(1, weight=0)
            self.form_card.grid_columnconfigure(2, weight=0)
            
            self.lbl_crit.grid(row=0, column=0, padx=15, pady=(15, 2), sticky="w")
            self.crit_menu.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="ew")
            self.lbl_texto.grid(row=2, column=0, padx=15, pady=(5, 2), sticky="w")
            self.texto_entry.grid(row=3, column=0, padx=15, pady=(0, 10), sticky="ew")
            self.lbl_accion.grid(row=4, column=0, padx=15, pady=(5, 2), sticky="w")
            self.accion_menu.grid(row=5, column=0, padx=15, pady=(0, 10), sticky="ew")
            
            self.lbl_dest.grid(row=6, column=0, padx=15, pady=(5, 2), sticky="w")
            self.dest_controls.grid(row=7, column=0, padx=15, pady=(0, 10), sticky="ew")
            self.subcarpeta_cb.grid(row=8, column=0, padx=15, pady=(5, 10), sticky="w")
            self.form_actions.grid(row=9, column=0, padx=15, pady=(0, 10), sticky="ew")
            self.lbl_templates.grid(row=10, column=0, padx=15, pady=(10, 2), sticky="w")
            self.templates_frame.grid(row=11, column=0, padx=15, pady=(0, 15), sticky="ew")
            
            # Controles de carpeta de origen (Vigilancia)
            self.watch_entry.pack_forget()
            self.btn_browse.pack_forget()
            self.btn_save_dir.pack_forget()
            
            self.watch_entry.pack(side="top", fill="x", expand=True, pady=(0, 10))
            self.btn_browse.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self.btn_save_dir.pack(side="right", fill="x", expand=True, padx=(5, 0))
            
            # Botones Rápidos de Vigilancia
            self.toggle_watch_btn.pack_forget()
            self.btn_go_rules.pack_forget()
            
            self.toggle_watch_btn.pack(side="top", fill="x", expand=True, pady=(0, 10))
            self.btn_go_rules.pack(side="top", fill="x", expand=True)
            
            # Botones Administrativos (Pestaña de Ajustes)
            self.btn_open_app.pack_forget()
            self.btn_open_log.pack_forget()
            self.btn_uninstall.pack_forget()
            self.btn_exit.pack_forget()
            
            self.btn_open_app.pack(side="top", fill="x", expand=True, pady=5)
            self.btn_open_log.pack(side="top", fill="x", expand=True, pady=5)
            self.btn_uninstall.pack(side="top", fill="x", expand=True, pady=5)
            self.btn_exit.pack(side="top", fill="x", expand=True, pady=5)
        else:
            # === DISEÑO HORIZONTAL (Sidebar Lateral) ===
            self.window.grid_columnconfigure(0, weight=0)
            self.window.grid_columnconfigure(1, weight=1)
            self.window.grid_rowconfigure(0, weight=1)
            self.window.grid_rowconfigure(1, weight=0)
            
            self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
            self.content_frame.grid(row=0, column=1, sticky="nsew")
            
            # Sidebar vertical
            for col in range(5):
                self.sidebar_frame.grid_columnconfigure(col, weight=0)
            self.sidebar_frame.grid_columnconfigure(0, weight=1)
            
            self.sidebar_frame.grid_rowconfigure(0, weight=0)
            self.sidebar_frame.grid_rowconfigure(1, weight=0)
            self.sidebar_frame.grid_rowconfigure(2, weight=0)
            self.sidebar_frame.grid_rowconfigure(3, weight=0)
            self.sidebar_frame.grid_rowconfigure(4, weight=1)
            self.sidebar_frame.grid_rowconfigure(5, weight=0)
            self.sidebar_frame.grid_rowconfigure(6, weight=0)
            
            self.sidebar_title.grid(row=0, column=0, padx=20, pady=(25, 20), sticky="w")
            self.btn_vigilancia.grid(row=1, column=0, padx=12, pady=5, sticky="ew")
            self.btn_reglas.grid(row=2, column=0, padx=12, pady=5, sticky="ew")
            self.btn_ajustes.grid(row=3, column=0, padx=12, pady=5, sticky="ew")
            
            self.sidebar_status_title.grid(row=5, column=0, padx=20, pady=(15, 0), sticky="w")
            self.sidebar_status_badge.grid(row=6, column=0, padx=15, pady=(5, 25), sticky="ew")
            self.sidebar_status_badge.configure(width=200)
            
            # Formulario de reglas (3 columnas horizontales)
            self.lbl_crit.grid_forget()
            self.crit_menu.grid_forget()
            self.lbl_texto.grid_forget()
            self.texto_entry.grid_forget()
            self.lbl_accion.grid_forget()
            self.accion_menu.grid_forget()
            self.lbl_dest.grid_forget()
            self.dest_controls.grid_forget()
            self.subcarpeta_cb.grid_forget()
            self.form_actions.grid_forget()
            self.lbl_templates.grid_forget()
            self.templates_frame.grid_forget()
            
            self.form_card.grid_columnconfigure(0, weight=1)
            self.form_card.grid_columnconfigure(1, weight=1)
            self.form_card.grid_columnconfigure(2, weight=1)
            
            self.lbl_crit.grid(row=0, column=0, padx=15, pady=(15, 3), sticky="w")
            self.crit_menu.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="ew")
            self.lbl_texto.grid(row=0, column=1, padx=10, pady=(15, 3), sticky="w")
            self.texto_entry.grid(row=1, column=1, padx=10, pady=(0, 15), sticky="ew")
            self.lbl_accion.grid(row=0, column=2, padx=15, pady=(15, 3), sticky="w")
            self.accion_menu.grid(row=1, column=2, padx=15, pady=(0, 15), sticky="ew")
            
            self.lbl_dest.grid(row=2, column=0, padx=15, pady=(0, 3), sticky="w")
            self.dest_controls.grid(row=3, column=0, columnspan=3, padx=15, pady=(0, 15), sticky="ew")
            self.subcarpeta_cb.grid(row=4, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")
            self.form_actions.grid(row=4, column=1, columnspan=2, padx=15, pady=(0, 15), sticky="e")
            self.lbl_templates.grid(row=5, column=0, columnspan=3, padx=15, pady=(0, 5), sticky="w")
            self.templates_frame.grid(row=6, column=0, columnspan=3, padx=15, pady=(0, 15), sticky="ew")
            
            # Controles de carpeta de origen
            self.watch_entry.pack_forget()
            self.btn_browse.pack_forget()
            self.btn_save_dir.pack_forget()
            
            self.watch_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
            self.btn_browse.pack(side="left", padx=(0, 10))
            self.btn_save_dir.pack(side="left")
            
            # Botones Rápidos de Vigilancia
            self.toggle_watch_btn.pack_forget()
            self.btn_go_rules.pack_forget()
            
            self.toggle_watch_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))
            self.btn_go_rules.pack(side="left", fill="x", expand=True)
            
            # Botones Administrativos
            self.btn_open_app.pack_forget()
            self.btn_open_log.pack_forget()
            self.btn_uninstall.pack_forget()
            self.btn_exit.pack_forget()
            
            self.btn_open_app.pack(side="left", fill="x", expand=True, padx=(0, 10))
            self.btn_open_log.pack(side="left", fill="x", expand=True, padx=(0, 10))
            self.btn_uninstall.pack(side="left", fill="x", expand=True, padx=(0, 10))
            self.btn_exit.pack(side="left", fill="x", expand=True)

    # --- Métodos de Interacción con Datos ---
    def load_gui_data(self):
        """Llena los inputs con la información actual de la configuración."""
        watch_path = self.config.get("carpeta_vigilada", "%USERPROFILE%\\Downloads")
        self.watch_entry.delete(0, "end")
        self.watch_entry.insert(0, watch_path)
        
        theme_es = self.config.get("tema_visual", "Oscuro")
        self.theme_var.set(theme_es)
        
        # Cargar lista de reglas
        self.render_rules()

    def browse_watch_folder(self):
        initial = expand_path(self.watch_entry.get())
        if not os.path.exists(initial):
            initial = os.path.expanduser("~")
        
        folder = ctk.filedialog.askdirectory(
            title="Seleccionar Carpeta a Vigilar",
            initialdir=initial
        )
        if folder:
            norm = os.path.normpath(folder)
            self.watch_entry.delete(0, "end")
            self.watch_entry.insert(0, norm)

    def update_watch_folder(self):
        new_path = self.watch_entry.get().strip()
        if not new_path:
            messagebox.showwarning("Campo Vacío", "Por favor, introduce una ruta válida.")
            return
            
        expanded = expand_path(new_path)
        if not os.path.exists(expanded):
            if messagebox.askyesno("Crear Carpeta", f"La carpeta:\n{new_path}\nno existe. ¿Deseas crearla?"):
                try:
                    os.makedirs(expanded, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo crear el directorio: {e}")
                    return
            else:
                return
                
        self.config["carpeta_vigilada"] = new_path
        if save_config(self.config_path, self.config):
            self.log(f"Ruta vigilada actualizada: {new_path}")
            messagebox.showinfo("Guardado", "Ruta de carpeta vigilada actualizada con éxito.")
            self.restart_watcher()
        else:
            messagebox.showerror("Error", "No se pudo actualizar el archivo de configuración.")

    def browse_destination_folder(self):
        initial = expand_path(self.destino_entry.get())
        if not os.path.exists(initial):
            initial = os.path.expanduser("~")
        
        folder = ctk.filedialog.askdirectory(
            title="Seleccionar Carpeta de Destino",
            initialdir=initial
        )
        if folder:
            norm = os.path.normpath(folder)
            self.destino_entry.delete(0, "end")
            self.destino_entry.insert(0, norm)

    def apply_quick_template(self, template_type):
        """Autocompleta el formulario usando plantillas intuitivas y dinámicas."""
        watch_path_raw = self.watch_entry.get().strip() or "%USERPROFILE%\\Downloads"
        watch_path = expand_path(watch_path_raw)
        
        if template_type == "pdf":
            self.criterio_var.set("Termina en")
            self.texto_entry.delete(0, "end")
            self.texto_entry.insert(0, ".pdf")
            self.accion_var.set("Mover")
            self.destino_entry.delete(0, "end")
            self.destino_entry.insert(0, os.path.join(watch_path, "PDFs"))
            self.subcarpeta_var.set(False)
            self.log("Plantilla PDF seleccionada y cargada en el formulario.")
            
        elif template_type == "imagenes":
            self.criterio_var.set("Termina en")
            self.texto_entry.delete(0, "end")
            self.texto_entry.insert(0, ".png")
            self.accion_var.set("Mover")
            self.destino_entry.delete(0, "end")
            self.destino_entry.insert(0, os.path.join(watch_path, "Imagenes"))
            self.subcarpeta_var.set(False)
            self.log("Plantilla Imágenes seleccionada y cargada en el formulario.")
            
        elif template_type == "videos":
            self.criterio_var.set("Termina en")
            self.texto_entry.delete(0, "end")
            self.texto_entry.insert(0, ".mp4")
            self.accion_var.set("Mover")
            self.destino_entry.delete(0, "end")
            self.destino_entry.insert(0, os.path.join(watch_path, "Videos"))
            self.subcarpeta_var.set(False)
            self.log("Plantilla Videos seleccionada y cargada en el formulario.")
            
        elif template_type == "comprimidos":
            self.criterio_var.set("Termina en")
            self.texto_entry.delete(0, "end")
            self.texto_entry.insert(0, ".zip")
            self.accion_var.set("Mover")
            self.destino_entry.delete(0, "end")
            self.destino_entry.insert(0, os.path.join(watch_path, "Comprimidos"))
            self.subcarpeta_var.set(False)
            self.log("Plantilla Comprimidos seleccionada y cargada en el formulario.")

    def render_rules(self):
        """Limpia y dibuja dinámicamente las reglas configuradas en el scroll frame."""
        for widget in self.rules_scrollable_frame.winfo_children():
            widget.destroy()
            
        reglas = self.config.get("reglas", [])
        if not reglas:
            no_rules = ctk.CTkLabel(
                self.rules_scrollable_frame,
                text="No hay ninguna regla configurada. Crea una en la parte superior.",
                font=("Segoe UI", 12, "italic"),
                text_color=("#707070", "#8E8E93")
            )
            no_rules.pack(pady=25)
            return

        for index, regla in enumerate(reglas):
            card = ctk.CTkFrame(self.rules_scrollable_frame, corner_radius=8, fg_color=("#FFFFFF", "#29292B"), border_width=1, border_color=("#E5E5EA", "#38383B"))
            card.pack(fill="x", padx=5, pady=4)
            
            criterio_en = regla.get("criterio", "")
            criterio_es = CRITERIO_EN_TO_ES.get(criterio_en, criterio_en)
            texto = regla.get("texto", "")
            
            accion_en = regla.get("accion", "")
            accion_es = ACCION_EN_TO_ES.get(accion_en, accion_en)
            
            destino = regla.get("carpeta_destino", "")
            subfolder = "Sí" if regla.get("crear_subcarpeta", False) else "No"
            
            if criterio_en == "autoclasificar":
                desc = f"🔍 AUTOCLASIFICACIÓN: Busca nombres con separador '{texto}'\n   ➔ {accion_es.upper()} en: {destino}"
            else:
                desc = f"• Si el archivo {criterio_es.lower()} '{texto}'\n   ➔ {accion_es.upper()} en: {destino} (Subcarpeta: {subfolder})"
            
            lbl = ctk.CTkLabel(
                card,
                text=desc,
                justify="left",
                font=("Segoe UI", 11),
                anchor="w",
                text_color=("#1A1A1A", "#E0E0E0")
            )
            lbl.pack(side="left", padx=15, pady=10, fill="x", expand=True)
            
            # Botones de Acción de Reglas (Eliminar y Editar)
            btn_del = ctk.CTkButton(
                card,
                text="Eliminar",
                width=75,
                height=28,
                fg_color="#D63031",
                hover_color="#C22021",
                text_color="#FFFFFF",
                font=("Segoe UI", 11, "bold"),
                command=lambda idx=index: self.delete_rule(idx)
            )
            btn_del.pack(side="right", padx=(0, 15), pady=10)

            btn_edit = ctk.CTkButton(
                card,
                text="Editar",
                width=75,
                height=28,
                fg_color=("#E5E5EA", "#0984E3"),
                hover_color=("#D1D1D6", "#086EB6"),
                text_color=("#1A1A1A", "#FFFFFF"),
                font=("Segoe UI", 11, "bold"),
                command=lambda idx=index: self.prepare_edit_rule(idx)
            )
            btn_edit.pack(side="right", padx=(0, 8), pady=10)

    def save_or_add_rule(self):
        criterio_es = self.criterio_var.get()
        criterio_en = CRITERIO_ES_TO_EN.get(criterio_es, "contiene")
        
        texto = self.texto_entry.get().strip()
        
        accion_es = self.accion_var.get()
        accion_en = ACCION_ES_TO_EN.get(accion_es, "mover")
        
        destino = self.destino_entry.get().strip()
        crear_subcarpeta = self.subcarpeta_var.get()
        
        if not texto:
            messagebox.showwarning("Campo Vacío", "Por favor ingresa un término de búsqueda o palabra clave.")
            return
            
        if not destino:
            messagebox.showwarning("Campo Vacío", "Por favor indica la carpeta de destino.")
            return

        expanded_dest = expand_path(destino)
        if not os.path.exists(expanded_dest):
            if messagebox.askyesno("Crear Carpeta", f"La carpeta destino:\n{destino}\nno existe. ¿Deseas crearla?"):
                try:
                    os.makedirs(expanded_dest, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo crear la carpeta de destino: {e}")
                    return
            else:
                return

        nueva_regla = {
            "criterio": criterio_en,
            "texto": texto,
            "accion": accion_en,
            "carpeta_destino": destino,
            "crear_subcarpeta": crear_subcarpeta
        }

        if "reglas" not in self.config:
            self.config["reglas"] = []

        if self.editing_rule_index != -1:
            # Modo Edición de Regla Existente
            idx = self.editing_rule_index
            self.config["reglas"][idx] = nueva_regla
            self.log(f"Regla modificada en índice {idx}: si {criterio_en} '{texto}'")
            
            # Restaurar Botón a su estado original
            self.editing_rule_index = -1
            self.add_rule_btn.configure(text="Añadir Regla", fg_color="#10AC84", hover_color="#0F9B75")
            self.cancel_edit_btn.pack_forget()
        else:
            # Añadir Nueva Regla
            self.config["reglas"].append(nueva_regla)
            self.log(f"Regla añadida: si {criterio_en} '{texto}'")

        if save_config(self.config_path, self.config):
            self.texto_entry.delete(0, "end")
            self.destino_entry.delete(0, "end")
            self.render_rules()
        else:
            messagebox.showerror("Error", "No se pudo actualizar el archivo de reglas.")

    def prepare_edit_rule(self, idx):
        """Rellena el formulario con los campos de la regla elegida para edición."""
        reglas = self.config.get("reglas", [])
        if 0 <= idx < len(reglas):
            regla = reglas[idx]
            self.editing_rule_index = idx
            
            criterio_en = regla.get("criterio", "contiene")
            criterio_es = CRITERIO_EN_TO_ES.get(criterio_en, "Contiene")
            self.criterio_var.set(criterio_es)
            
            self.texto_entry.delete(0, "end")
            self.texto_entry.insert(0, regla.get("texto", ""))
            
            accion_en = regla.get("accion", "mover")
            accion_es = ACCION_EN_TO_ES.get(accion_en, "Mover")
            self.accion_var.set(accion_es)
            
            self.destino_entry.delete(0, "end")
            self.destino_entry.insert(0, regla.get("carpeta_destino", ""))
            self.subcarpeta_var.set(regla.get("crear_subcarpeta", True))
            
            # Cambiar apariencia del botón
            self.add_rule_btn.configure(text="Guardar Cambios", fg_color="#6C5CE7", hover_color="#5B4CC4")
            self.cancel_edit_btn.pack(side="left", padx=(0, 10))

    def cancel_edit(self):
        """Cancela la edición de una regla activa y limpia el formulario."""
        self.editing_rule_index = -1
        self.texto_entry.delete(0, "end")
        self.destino_entry.delete(0, "end")
        self.add_rule_btn.configure(text="Añadir Regla", fg_color="#10AC84", hover_color="#0F9B75")
        self.cancel_edit_btn.pack_forget()

    def delete_rule(self, index):
        reglas = self.config.get("reglas", [])
        if 0 <= index < len(reglas):
            regla = reglas.pop(index)
            self.config["reglas"] = reglas
            if save_config(self.config_path, self.config):
                self.log(f"Regla eliminada: si {regla.get('criterio')} '{regla.get('texto')}'")
                self.render_rules()
                # Si estábamos editando la regla eliminada, cancelar edición
                if self.editing_rule_index == index:
                    self.cancel_edit()
                elif self.editing_rule_index > index:
                    self.editing_rule_index -= 1
            else:
                messagebox.showerror("Error", "No se pudo actualizar el archivo de reglas.")

    # --- Control del Visualizer ---
    def change_theme(self, choice=None):
        theme_es = self.theme_var.get()
        theme_en = THEME_ES_TO_EN.get(theme_es, "System")
        ctk.set_appearance_mode(theme_en)
        
        self.config["tema_visual"] = theme_es
        save_config(self.config_path, self.config)
        self.log(f"Tema visual establecido en: {theme_es}")

    def open_app_directory(self):
        try:
            os.startfile(SCRIPT_DIR)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el directorio de la aplicación: {e}")

    def open_log_in_notepad(self):
        if os.path.exists(self.log_file):
            try:
                os.startfile(self.log_file)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo abrir el archivo de log: {e}")
        else:
            messagebox.showwarning("Log vacío", "El archivo de logs no ha sido creado todavía.")

    # --- Lógica de Desinstalación Completa ---
    def uninstall_app(self):
        """Detiene el servicio, quita el arranque y elimina todos los archivos del programa."""
        if messagebox.askyesno(
            "Confirmar Desinstalación Completa",
            "¿Estás seguro de que deseas desinstalar COMPLETAMENTE la aplicación?\n\n"
            "Esto detendrá el servicio, eliminará el arranque automático de Windows "
            "y borrará permanentemente la carpeta del programa con todos sus archivos."
        ):
            self.stop_watcher_internal()
            
            # Detener el icono de la bandeja de sistema si existe
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except Exception as e:
                    self.log(f"No se pudo detener el icono de la bandeja: {e}")
            
            # Eliminar accesos directos de Inicio (Startup)
            startup_dir = os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Start Menu\Programs\Startup"
            )
            shortcuts = ["OrganizadorVisualArchivos.lnk", "OrganizadorDeArchivos.lnk"]
            
            for file in shortcuts:
                path = os.path.join(startup_dir, file)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        self.log(f"Acceso directo de inicio eliminado: {file}")
                    except Exception as e:
                        self.log(f"No se pudo eliminar el acceso directo de inicio {file}: {e}")
            
            # Intentar obtener la ruta del Escritorio de forma robusta en Windows
            desktop_paths = []
            try:
                import ctypes
                from ctypes import wintypes
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                # CSIDL_DESKTOP = 0
                ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
                if buf.value:
                    desktop_paths.append(buf.value)
            except Exception:
                pass
                
            # Fallbacks comunes para la ruta del Escritorio
            home = os.path.expanduser("~")
            desktop_paths.append(os.path.join(home, "Desktop"))
            desktop_paths.append(os.path.join(home, "OneDrive", "Desktop"))
            
            # Eliminar acceso directo del Escritorio
            for dp in desktop_paths:
                path = os.path.join(dp, "Organizador de Archivos.lnk")
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        self.log(f"Acceso directo del Escritorio eliminado: {path}")
                    except Exception as e:
                        self.log(f"No se pudo eliminar el acceso directo del Escritorio {path}: {e}")
            
            # Mostrar mensaje informativo de cierre
            messagebox.showinfo(
                "Desinstalación Iniciada",
                "La aplicación se cerrará ahora y la carpeta del organizador será eliminada permanentemente del equipo."
            )
            
            # Programar eliminación diferida de la carpeta del script en Windows.
            # Usamos el PID de Python para esperar a que el proceso principal termine por completo
            # antes de intentar borrar la carpeta.
            import subprocess
            folder_to_delete = SCRIPT_DIR
            pid = os.getpid()
            
            # Comando PowerShell:
            # 1. Espera a que termine el proceso de Python (Timeout de 15 segundos para evitar bucles infinitos)
            # 2. Cambia la ubicación de trabajo a TEMP para evitar que PowerShell bloquee la carpeta
            # 3. Elimina la carpeta de forma recursiva y forzada
            cmd = (
                f"Start-Sleep -Seconds 1; "
                f"$proc = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                f"if ($proc) {{ $proc | Wait-Process -Timeout 15 }}; "
                f"Set-Location -Path $env:TEMP; "
                f"Remove-Item -Path '{folder_to_delete}' -Recurse -Force"
            )
            
            try:
                subprocess.Popen(
                    ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                    cwd=os.environ.get("TEMP", "C:\\")  # Iniciar en TEMP para evitar bloqueos del CWD
                )
            except Exception as e:
                self.log(f"Error al iniciar desinstalador diferido: {e}")
                
            self.window.after(0, self.window.destroy)
            sys.exit(0)

    # --- Control del Motor Watchdog ---
    def start_watcher_internal(self):
        with self.watcher_lock:
            if self.observer is not None:
                return
            
            watch_path_raw = self.config.get("carpeta_vigilada", "%USERPROFILE%\\Downloads")
            watch_path = expand_path(watch_path_raw)
            
            if not os.path.exists(watch_path):
                try:
                    os.makedirs(watch_path, exist_ok=True)
                    self.log(f"Creando carpeta vigilada inexistente: {watch_path}")
                except Exception as e:
                    self.log(f"Error al crear carpeta a vigilar: {e}")
                    self.update_status_ui(active=False)
                    return
            
            self.observer = Observer()
            self.event_handler = OrganizerHandler(self.config_path, self.log)
            self.observer.schedule(self.event_handler, path=watch_path, recursive=False)
            self.observer.start()
            self.update_status_ui(active=True)
            self.log(f"Vigilancia activa en: {watch_path}")

    def stop_watcher_internal(self):
        with self.watcher_lock:
            if self.observer is not None:
                self.observer.stop()
                self.observer = None
                self.update_status_ui(active=False)
                self.log("Vigilancia detenida.")

    def restart_watcher(self):
        def _task():
            with self.watcher_lock:
                if self.observer is not None:
                    self.observer.stop()
                    self.observer.join()
                    self.observer = None
            self.window.after(0, self.start_watcher_internal)
        threading.Thread(target=_task, daemon=True).start()

    def toggle_watcher(self):
        if self.observer is not None:
            self.stop_watcher_internal()
        else:
            self.start_watcher_internal()

    def update_status_ui(self, active=True):
        """Actualiza todos los textos y colores del estado en el panel visual."""
        if active:
            self.sidebar_status_badge.configure(
                text="● VIGILANDO",
                text_color=("#1E8449", "#2ECC71"),
                fg_color=("#D5F5E3", "#1D3C25")
            )
            if hasattr(self, "status_text_large"):
                self.status_text_large.configure(text="Vigilancia Activa", text_color=("#1E8449", "#2ECC71"))
            if hasattr(self, "toggle_watch_btn"):
                self.toggle_watch_btn.configure(
                    text="Pausar Vigilancia",
                    fg_color="#D63031",
                    hover_color="#C22021"
                )
        else:
            self.sidebar_status_badge.configure(
                text="○ PAUSADO",
                text_color=("#B03A2E", "#E74C3C"),
                fg_color=("#FADBD8", "#3D1E1E")
            )
            if hasattr(self, "status_text_large"):
                self.status_text_large.configure(text="Vigilancia Pausada", text_color=("#B03A2E", "#E74C3C"))
            if hasattr(self, "toggle_watch_btn"):
                self.toggle_watch_btn.configure(
                    text="Reanudar Vigilancia",
                    fg_color="#27AE60",
                    hover_color="#219653"
                )

    # --- Sistema Tray / Segundo Plano ---
    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar Panel", self.restore_window_action, default=True),
            pystray.MenuItem("Apagar Organizador", self.quit_app)
        )
        self.tray_icon = pystray.Icon(
            "organizador",
            self.create_tray_image(),
            "Organizador Inteligente",
            menu=menu
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def create_tray_image(self):
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(logo_path):
            try:
                # Cargar el logo y redimensionarlo a 64x64 para la bandeja de sistema
                return Image.open(logo_path).resize((64, 64), Image.Resampling.LANCZOS)
            except Exception as e:
                self.log(f"No se pudo cargar el logo de la bandeja de sistema: {e}")
                
        # Fallback al dibujo geométrico por defecto
        image = Image.new('RGB', (64, 64), color=(108, 92, 231))
        dc = ImageDraw.Draw(image)
        dc.rectangle([14, 22, 50, 48], fill=(255, 255, 255))
        dc.rectangle([18, 26, 46, 44], fill=(108, 92, 231))
        dc.rectangle([14, 16, 28, 22], fill=(255, 255, 255))
        return image

    def restore_window_action(self, icon=None, item=None):
        self.window.after(0, self._deiconify_window)

    def _deiconify_window(self):
        self.window.deiconify()
        self.window.state("normal")
        self.window.focus_force()

    def on_close_window(self):
        self.window.withdraw()

    def quit_app(self, icon=None, item=None):
        self.log("Apagando aplicación por completo...")
        self.stop_watcher_internal()
        if self.tray_icon:
            self.tray_icon.stop()
        self.window.after(0, self.window.destroy)
        sys.exit(0)

    def log(self, message):
        """Registra un mensaje simple con timestamp en consola y archivo log."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        formatted = f"{timestamp} - {message}"
        print(formatted)
        logging.info(message)


import socket

# Puerto de comunicación para restaurar la ventana de la instancia activa
LOCK_PORT = 28461

def check_single_instance_or_restore():
    """Intenta conectarse al puerto de control. Si tiene éxito, envía la señal de mostrar ventana y sale."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(('127.0.0.1', LOCK_PORT))
        s.sendall(b"SHOW")
        s.close()
        return False  # Ya hay una instancia ejecutándose, se le notificó con éxito
    except socket.error:
        return True   # Somos la primera instancia activa

def listen_for_restore_signals(app):
    """Bucle servidor TCP en segundo plano para escuchar peticiones de restauración de ventana."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(('127.0.0.1', LOCK_PORT))
        server.listen(5)
    except socket.error as e:
        app.log(f"Error al iniciar el servidor de restauración de instancia única: {e}")
        return
        
    while True:
        try:
            conn, addr = server.accept()
            data = conn.recv(1024).decode('utf-8').strip()
            if data == "SHOW":
                app.log("Recibida señal externa para mostrar panel. Restaurando ventana...")
                app.window.after(0, app.restore_window_action)
            conn.close()
        except Exception:
            break


def main():
    if not check_single_instance_or_restore():
        # Salir silenciosamente ya que notificamos a la instancia principal
        sys.exit(0)

    start_minimized = "--background" in sys.argv
    app = OrganizadorApp(start_minimized=start_minimized)
    
    # Iniciar servidor de escucha en segundo plano para restaurar desde accesos directos
    threading.Thread(target=listen_for_restore_signals, args=(app,), daemon=True).start()
    
    app.window.mainloop()


if __name__ == "__main__":
    main()
