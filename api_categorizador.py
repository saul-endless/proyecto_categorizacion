from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse
import shutil
import os
import sys
import json
import importlib.util
import gc
from pathlib import Path
from typing import List, Optional
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor


sys.path.append("/home/endless/FUNCIONALIDADES/PROYECTO EXTRACTOR")
import sms_ai

"""
import logging
import http.client

# --- INICIO DE DEPURACIÓN DE RED ---

http.client.HTTPConnection.debuglevel = 1

logging.basicConfig(

    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',

    level=logging.DEBUG

)

logging.getLogger("urllib3").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)

# --- FIN DE DEPURACIÓN DE RED ---
"""

# Define las rutas base del sistema
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

# Define el directorio específico para el chatbot dentro del extractor
CHATBOT_INPUT_BASE = DIR_EXTRACTOR / "input_chatbot"

# Crea los directorios necesarios si no existen
for d in [ORQ_INPUT, ORQ_OUTPUT, EXT_INPUT, EXT_OUTPUT, MOD_INPUT, MOD_OUTPUT, CHATBOT_INPUT_BASE]:
    d.mkdir(parents=True, exist_ok=True)

# Inicializa la aplicación FastAPI
app = FastAPI(title="API Orquestador Financiero")

# Inicializa el semáforo para la gestión de la GPU
gpu_sem = asyncio.Semaphore(1) 

# Inicializa el diccionario para almacenar trabajos en memoria
trabajos = {}
# Inicializa el almacén de sesiones de chat
CHAT_SESSIONS = {} 

executor_gpu = ThreadPoolExecutor(max_workers=1)
executor_chat = ThreadPoolExecutor(max_workers=10)

# Agrega la ruta del extractor al sistema e importa el módulo
sys.path.append(str(DIR_EXTRACTOR))
try:
    import extractor_gemini_ai as extractor_ia
except ImportError:
    print("Error CRITICO: No se encontro el modulo extractor_ia.py")

def limpiar_directorio(directorio: Path):
    # Elimina todos los archivos dentro del directorio especificado
    if directorio.exists():
        for archivo in directorio.glob("*"):
            if archivo.is_file():
                archivo.unlink()

def cargar_modulo_con_espacios(ruta_archivo, nombre_modulo):
    # Carga dinámicamente un módulo desde una ruta que puede contener espacios
    spec = importlib.util.spec_from_file_location(nombre_modulo, ruta_archivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre_modulo] = modulo
    spec.loader.exec_module(modulo)
    return modulo

def obtener_ultimo_archivo(directorio, patron="*"):
    # Obtiene el archivo más reciente modificado en un directorio según un patrón
    archivos = list(directorio.glob(patron))
    return max(archivos, key=os.path.getctime) if archivos else None

# --- LÓGICA DE EJECUCIÓN EN FONDO (WORKERS) ---

def ejecutar_extraccion_sync(job_id: str, pdf_path: Path):
    """
    Ejecuta la lógica de extracción de datos (Fase 1) de forma síncrona.
    """
    trabajos[job_id]["status"] = "procesando"
    
    out_aislado = EXT_OUTPUT / f"out_{job_id}"
    out_aislado.mkdir(parents=True, exist_ok=True)
    
    try:
        print(f"\n--- [WORKER] INICIANDO FASE 1 (IA): {pdf_path.name} ---")
        
        # Ejecuta el extractor cambiando el directorio de trabajo temporalmente
        original_cwd = os.getcwd()
        try:
            os.chdir(DIR_EXTRACTOR)
            extractor_ia.main_extraction_ia(str(pdf_path), str(out_aislado))
        except Exception as e:
            print(f"Error critico en extractor IA: {e}")
            raise e
        finally:
            os.chdir(original_cwd)
        
        # Recopila los resultados generados
        resultados = {}
        archivos_generados = list(out_aislado.glob("*.json"))
        
        if not archivos_generados:
            raise Exception("El extractor IA no generó archivos JSON.")

        for json_file in archivos_generados:
            # Copia el archivo generado al output del orquestador
            dest = ORQ_OUTPUT / json_file.name
            shutil.copy(json_file, dest)
            
            # Lee y carga el contenido del JSON para la respuesta
            with open(json_file, "r", encoding="utf-8") as f:
                content = json.load(f)
                key = "OTROS"
                if "_DATOS" in json_file.name.upper(): key = "DATOS"
                elif "_INGRESOS" in json_file.name.upper(): key = "INGRESOS"
                elif "_EGRESOS" in json_file.name.upper(): key = "EGRESOS"
                
                resultados[key] = {
                    "filename": json_file.name,
                    "data": content
                }
        
        print("--- [WORKER] FASE 1 COMPLETADA ---")
        trabajos[job_id]["status"] = "completado"
        trabajos[job_id]["resultado"] = resultados

    except Exception as e:
        print(f"Error general Fase 1 Worker: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)
    finally:
        if out_aislado.exists():
            shutil.rmtree(out_aislado)

def ejecutar_categorizacion_sync(job_id: str, archivos_guardados: list):
    try:
        print("\n--- [WORKER] INICIANDO FASE 2: CATEGORIZACION ---")
        trabajos[job_id]["status"] = "procesando"
        
        # Carga el módulo de procesamiento de modelos
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
        
        if 'CUDA_VISIBLE_DEVICES' in os.environ: del os.environ['CUDA_VISIBLE_DEVICES']
        
        original_cwd = os.getcwd()
        os.chdir(DIR_MODELOS)
        
        try:
            print("Cargando modelo Llama en RTX 5090...")
            # Carga el modelo LLM
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
            resultados_categorizados = {}

            for nombre_archivo in archivos_guardados:
                if "_INGRESOS" in nombre_archivo.upper() or "_EGRESOS" in nombre_archivo.upper():
                    print(f"Procesando {nombre_archivo}...")
                    # Ejecuta la lógica de fusión y categorización
                    mod.procesar_logica_fusion(llm, nombre_archivo)
                    
                    nombre_base = Path(nombre_archivo).stem
                    patron = f"ANALISIS_{nombre_base}_*.json"
                    res = obtener_ultimo_archivo(MOD_OUTPUT, patron)
                    
                    if res:
                        nuevo_nombre = f"{nombre_base}_CON_GIROS.json"
                        dest_final = ORQ_OUTPUT / nuevo_nombre
                        shutil.copy(res, dest_final)
                        
                        with open(dest_final, "r", encoding="utf-8") as f:
                            resultados_categorizados[nombre_archivo] = json.load(f)
                    else:
                        print(f"Advertencia: No se genero salida para {nombre_archivo}")
                        resultados_categorizados[nombre_archivo] = {"error": "El modelo no generó salida"}

            print("--- [WORKER] FASE 2 COMPLETADA ---")
            trabajos[job_id]["status"] = "completado"
            trabajos[job_id]["resultado"] = resultados_categorizados

        finally:
            # Libera memoria y restablece el directorio de trabajo
            if 'llm' in locals(): del llm
            gc.collect()
            os.chdir(original_cwd)

    except Exception as e:
        print(f"Error Fase 2: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)

def ejecutar_perfilado_sync(job_id: str, nombre_real_datos: str):
    try:
        print("\n--- [WORKER] EJECUTANDO PERFILADO ---")
        trabajos[job_id]["status"] = "procesando"
        
        # Carga el módulo de modelos
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
        
        original_cwd = os.getcwd()
        os.chdir(DIR_MODELOS)
        
        try:
            print("Cargando modelo de perfilado...")
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
            # Ejecuta el perfilado empresarial
            mod.procesar_perfilado_empresarial(llm, "TEMP_EXEC_DATOS.json")
            
            out_path = MOD_OUTPUT / "TEMP_EXEC_DATOS.json"
            
            if out_path.exists():
                nombre_final = Path(nombre_real_datos).stem + "_PERFIL.json"
                dest_final = ORQ_OUTPUT / nombre_final
                
                shutil.copy(out_path, dest_final)
                
                with open(dest_final, "r", encoding="utf-8") as f:
                    print("--- [WORKER] FASE 3 COMPLETADA ---")
                    trabajos[job_id]["status"] = "completado"
                    trabajos[job_id]["resultado"] = json.load(f)
            else:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = "El modelo no generó el perfil."

        finally:
            # Libera memoria y restablece directorio
            if 'llm' in locals(): del llm
            gc.collect()
            os.chdir(original_cwd)

    except Exception as e:
        print(f"Error Fase 3: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)

# -----------------------------------------------------------------------------
# WORKER FASE 3.5: BIENVENIDA Y SUGERENCIAS (TOTALMENTE INDEPENDIENTE)
# -----------------------------------------------------------------------------
def ejecutar_bienvenida_independiente_sync(job_id: str, archivos_temp_paths: List[Path], carpeta_temporal: Path):
    try:
        print(f"\n--- [WORKER] FASE 3.5 (INDEPENDIENTE) | JOB: {job_id} ---")
        trabajos[job_id]["status"] = "procesando"

        # 1. INVOCA AL CHATBOT PARA GENERAR SUGERENCIAS
        ruta_chatbot = DIR_EXTRACTOR / "chatbot.py"
        chatbot_module = cargar_modulo_con_espacios(ruta_chatbot, "modulo_chatbot")
        
        ruta_modelo = chatbot_module.ruta_modelo_llm 
        modelo = chatbot_module.iniciar_modelo(ruta_modelo)
        
        if not modelo:
            raise Exception("No se pudo iniciar el modelo para la fase 3.5.")

        # 2. LEE LOS ARCHIVOS DE LA CARPETA EFÍMERA
        contexto_datos = chatbot_module.leer_archivos_json(carpeta_temporal)
        
        # 3. GENERA LA RESPUESTA
        respuesta_sugerencias = chatbot_module.generar_bienvenida_cfo(modelo, contexto_datos)

        print("--- [WORKER] FASE 3.5 COMPLETADA ---")
        trabajos[job_id]["status"] = "completado"
        trabajos[job_id]["resultado"] = {
            "respuesta": respuesta_sugerencias
        }

    except Exception as e:
        print(f"Error Fase 3.5: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)
    finally:
        # Libera RAM de la GPU/Modelo
        if 'modelo' in locals(): del modelo
        gc.collect()
        
        # 4. LIMPIEZA ABSOLUTA (Elimina los JSON para que sea 100% stateless)
        try:
            if carpeta_temporal.exists():
                shutil.rmtree(carpeta_temporal)
                print(f"Limpieza F3.5: Carpeta temporal eliminada ({carpeta_temporal.name})")
        except Exception as e:
            pass

# -----------------------------------------------------------------------------
# WORKER FASE 3.7: ANÁLISIS COMPARATIVO HISTÓRICO (INDEPENDIENTE)
# -----------------------------------------------------------------------------
def ejecutar_comparativa_independiente_sync(job_id: str, archivos_temp_paths: List[Path], carpeta_temporal: Path):
    try:
        print(f"\n--- [WORKER] FASE 3.7 (COMPARATIVA) | JOB: {job_id} ---")
        trabajos[job_id]["status"] = "procesando"

        # 1. INVOCA AL MÓDULO PARA GENERAR ANÁLISIS
        ruta_chatbot = DIR_EXTRACTOR / "chatbot.py"
        chatbot_module = cargar_modulo_con_espacios(ruta_chatbot, "modulo_chatbot")
        
        ruta_modelo = chatbot_module.ruta_modelo_llm 
        modelo = chatbot_module.iniciar_modelo(ruta_modelo)
        
        if not modelo:
            raise Exception("No se pudo iniciar el modelo para la fase 3.7.")

        # 2. LEE LOS ARCHIVOS DE LA CARPETA EFÍMERA
        contexto_datos = chatbot_module.leer_archivos_json(carpeta_temporal)
        
        # 3. GENERA EL REPORTE COMPARATIVO (Ajusta el nombre de la función aquí si es diferente)
        respuesta_comparativa = chatbot_module.generar_analisis_fase_3_7(modelo, contexto_datos)

        print("--- [WORKER] FASE 3.7 COMPLETADA ---")
        trabajos[job_id]["status"] = "completado"
        trabajos[job_id]["resultado"] = {
            "respuesta": respuesta_comparativa
        }

    except Exception as e:
        print(f"Error Fase 3.7: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)
    finally:
        # Libera RAM de la GPU/Modelo
        if 'modelo' in locals(): del modelo
        gc.collect()
        
        # 4. LIMPIEZA ABSOLUTA (Stateless)
        try:
            if carpeta_temporal.exists():
                shutil.rmtree(carpeta_temporal)
                print(f"Limpieza F3.7: Carpeta temporal eliminada ({carpeta_temporal.name})")
        except Exception as e:
            pass

def ejecutar_chatbot_sync(job_id: str, session_id: str, archivos_temp_paths: List[Path], pregunta_usuario: str, es_nueva_sesion: bool, temp_ingest_dir: Path = None):
    """
    Ejecuta la lógica del Chatbot (Fase 4) gestionando memoria de sesión.
    """
    try:
        print(f"\n--- [WORKER] CHATBOT | SESIÓN: {session_id} ---")
        trabajos[job_id]["status"] = "procesando"

        # 1. GESTIONA CARPETAS Y ARCHIVOS
        if es_nueva_sesion:
            nombre_empresa_raw = None
            
            # Busca en los archivos temporales para extraer el nombre (Si hay archivos)
            if archivos_temp_paths:
                for temp_path in archivos_temp_paths:
                    if "_DATOS" in temp_path.name.upper():
                        try:
                            with open(temp_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                nombre_empresa_raw = data.get("Nombre de la empresa del estado de cuenta")
                                if nombre_empresa_raw: break
                        except Exception: pass

            if nombre_empresa_raw:
                # Se agrega el session_id para garantizar que la carpeta sea única por cada chat
                nombre_carpeta = f"{nombre_empresa_raw.strip().replace(' ', '_')}_{session_id}"
            else:
                nombre_carpeta = f"EMPRESA_DESCONOCIDA_{session_id}"

            dir_empresa = CHATBOT_INPUT_BASE / nombre_carpeta
            dir_empresa.mkdir(parents=True, exist_ok=True)

            # Mueve los archivos a la carpeta definitiva (Si existen)
            if archivos_temp_paths:
                for temp_path in archivos_temp_paths:
                    dest = dir_empresa / temp_path.name
                    shutil.move(str(temp_path), str(dest))
            
            # --- EJECUTA LIMPIEZA SEGURA: Borra carpeta TEMP_INGEST vacía ---
            try:
                if temp_ingest_dir and temp_ingest_dir.exists():
                    shutil.rmtree(temp_ingest_dir)
                    print(f"Limpieza: Carpeta temporal eliminada ({temp_ingest_dir.name})")
            except Exception as e:
                print(f"Nota: No se pudo borrar carpeta temp (no afecta al sistema): {e}")
            # ---------------------------------------------------------------

            # Guarda la ruta en la sesión
            CHAT_SESSIONS[session_id]["ruta_datos"] = dir_empresa
            print(f"Contexto guardado para sesión {session_id} en: {dir_empresa}")

        # 2. RECUPERA EL CONTEXTO DE LA SESIÓN
        dir_datos_sesion = CHAT_SESSIONS[session_id].get("ruta_datos")
        historial_actual = CHAT_SESSIONS[session_id]["historial"]

        # Si por alguna razón no existe el directorio, crea uno vacío para que no rompa el flujo
        if not dir_datos_sesion or not dir_datos_sesion.exists():
            dir_datos_sesion = CHATBOT_INPUT_BASE / f"EMPRESA_DESCONOCIDA_{session_id}"
            dir_datos_sesion.mkdir(parents=True, exist_ok=True)
            CHAT_SESSIONS[session_id]["ruta_datos"] = dir_datos_sesion

        # 3. INVOCA AL CHATBOT
        ruta_chatbot = DIR_EXTRACTOR / "chatbot.py"
        chatbot_module = cargar_modulo_con_espacios(ruta_chatbot, "modulo_chatbot")
        
        # Carga el Modelo
        ruta_modelo = chatbot_module.ruta_modelo_llm 
        modelo = chatbot_module.iniciar_modelo(ruta_modelo)
        
        if not modelo:
            raise Exception("No se pudo iniciar el modelo del chatbot.")

        # Lee los datos usando la ruta de la sesión
        contexto_datos = chatbot_module.leer_archivos_json(dir_datos_sesion)
        
        print(f"Generando respuesta para: '{pregunta_usuario}' usando historial de {len(historial_actual)} turnos.")
        
        # Genera la respuesta pasando el historial acumulado
        respuesta = chatbot_module.generar_respuesta_chat(modelo, contexto_datos, historial_actual, pregunta_usuario)

        # 4. ACTUALIZA LA MEMORIA GLOBAL
        CHAT_SESSIONS[session_id]["historial"].append({"q": pregunta_usuario, "a": respuesta})
        
        # Limita el historial a los últimos 15 turnos
        if len(CHAT_SESSIONS[session_id]["historial"]) > 15:
            CHAT_SESSIONS[session_id]["historial"].pop(0)

        print("--- [WORKER] FASE 4 COMPLETADA ---")
        trabajos[job_id]["status"] = "completado"
        trabajos[job_id]["resultado"] = {
            "empresa": str(dir_datos_sesion.name),
            "respuesta": respuesta,
            "historial_len": len(CHAT_SESSIONS[session_id]["historial"])
        }

    except Exception as e:
        print(f"Error Fase 4: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)
    finally:
        if 'modelo' in locals(): del modelo
        gc.collect()

# --- ENDPOINTS ---

@app.get("/")
def home():
    return {"estado": "API Activa", "gpu": "RTX 5090 Lista", "modo": "Asincrono"}

@app.get("/estado/{job_id}")
async def obtener_estado(job_id: str):
    if job_id not in trabajos:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    return trabajos[job_id]

@app.post("/fase1/extraer")
async def extraer_pdf(file: UploadFile = File(...)):
    try:
        job_id = str(uuid.uuid4())
        limpiar_directorio(EXT_INPUT)
        pdf_path = EXT_INPUT / file.filename
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        print(f"Solicitud recibida F1. Job ID: {job_id}")
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        executor_gpu.submit(ejecutar_extraccion_sync, job_id, pdf_path)
        return {"job_id": job_id, "status": "iniciado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase2/categorizar")
async def categorizar_transacciones(files: List[UploadFile] = File(...)):
    try:
        limpiar_directorio(MOD_INPUT)
        limpiar_directorio(MOD_OUTPUT)
        nombres_procesados = []
        for file in files:
            dest_path = MOD_INPUT / file.filename
            content = await file.read()
            with open(dest_path, "wb") as buffer:
                buffer.write(content)
            nombres_procesados.append(file.filename)

        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        executor_gpu.submit(ejecutar_categorizacion_sync, job_id, nombres_procesados)
        return {"job_id": job_id, "status": "iniciado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase3/perfilar")
async def perfilar_empresa(files: List[UploadFile] = File(...)):
    try:
        limpiar_directorio(MOD_INPUT)
        limpiar_directorio(MOD_OUTPUT)

        nombre_real_datos = None
        for file in files:
            upper_name = file.filename.upper()
            nombre_temp = file.filename
            
            if "_DATOS" in upper_name:
                nombre_real_datos = file.filename
                nombre_temp = "TEMP_EXEC_DATOS.json"
            elif "_INGRESOS" in upper_name:
                nombre_temp = "TEMP_EXEC_INGRESOS_CON_GIROS.json"
            elif "_EGRESOS" in upper_name:
                nombre_temp = "TEMP_EXEC_EGRESOS_CON_GIROS.json"
            
            dest_path = MOD_INPUT / nombre_temp
            content = await file.read()
            with open(dest_path, "wb") as buffer:
                buffer.write(content)

        if not nombre_real_datos:
            raise HTTPException(status_code=400, detail="Falta archivo _DATOS")

        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        executor_gpu.submit(ejecutar_perfilado_sync, job_id, nombre_real_datos)
        return {"job_id": job_id, "status": "iniciado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase3_5/bienvenida")
async def generar_bienvenida_independiente(
    files: List[UploadFile] = File(None)
):
    """
    Endpoint Fase 3.5 (Stateless): Recibe archivos, genera opciones de preguntas y destruye los archivos.
    No interactúa con CHAT_SESSIONS ni con la Fase 4. Tolerante a peticiones sin archivos.
    """
    try:
        job_id = str(uuid.uuid4())
        saved_paths = []

        # Crea una carpeta estrictamente temporal para este job
        temp_dir = CHATBOT_INPUT_BASE / f"TEMP_F35_{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        if files:
            for file in files:
                dest_path = temp_dir / file.filename
                content = await file.read()
                with open(dest_path, "wb") as buffer:
                    buffer.write(content)
                saved_paths.append(dest_path)

        num_files = len(files) if files else 0
        print(f"Solicitud API F3.5. Job: {job_id} | Archivos recibidos: {num_files}")
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        
        # Ejecuta el proceso en el pool para no bloquear el API (Se le pasa temp_dir para evitar fallos si no hay archivos)
        executor_chat.submit(ejecutar_bienvenida_independiente_sync, job_id, saved_paths, temp_dir)
        
        return {"job_id": job_id, "status": "iniciado"}

    except Exception as e:
        print(f"Error endpoint fase 3.5: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase3_7/comparar")
async def generar_comparativa_independiente(
    files: List[UploadFile] = File(...)
):
    """
    Endpoint Fase 3.7 (Stateless): Recibe estados de cuenta históricos y actuales, 
    genera el análisis comparativo en HTML/LaTeX y destruye los archivos.
    """
    try:
        job_id = str(uuid.uuid4())
        saved_paths = []

        # Crea una carpeta estrictamente temporal para este job
        temp_dir = CHATBOT_INPUT_BASE / f"TEMP_F37_{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        if files:
            for file in files:
                dest_path = temp_dir / file.filename
                content = await file.read()
                with open(dest_path, "wb") as buffer:
                    buffer.write(content)
                saved_paths.append(dest_path)

        num_files = len(files) if files else 0
        print(f"Solicitud API F3.7. Job: {job_id} | Archivos recibidos: {num_files}")
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        
        # Ejecuta el proceso en el pool de chat (usa pocos recursos de RAM)
        executor_chat.submit(ejecutar_comparativa_independiente_sync, job_id, saved_paths, temp_dir)
        
        return {"job_id": job_id, "status": "iniciado"}

    except Exception as e:
        print(f"Error endpoint fase 3.7: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase4/chat")
async def chatbot_consultar(
    files: List[UploadFile] = File(None),
    pregunta: str = Form(...),
    session_id: str = Form(...)
):
    """
    Endpoint Chatbot con soporte de SESIONES y tolerante a peticiones sin archivos.
    """
    try:
        job_id = str(uuid.uuid4())
        es_nueva_sesion = False
        saved_paths = []
        temp_ingest_dir = None

        # Verifica si la sesión existe en memoria
        if session_id not in CHAT_SESSIONS:
            print(f"Nueva sesión detectada: {session_id}")
            CHAT_SESSIONS[session_id] = {"historial": [], "ruta_datos": None}
            es_nueva_sesion = True
            
            # Guarda archivos temporalmente para que el worker los procese
            temp_ingest_dir = CHATBOT_INPUT_BASE / f"TEMP_INGEST_{job_id}"
            temp_ingest_dir.mkdir(parents=True, exist_ok=True)
            
            if files:
                for file in files:
                    dest_path = temp_ingest_dir / file.filename
                    content = await file.read()
                    with open(dest_path, "wb") as buffer:
                        buffer.write(content)
                    saved_paths.append(dest_path)
        else:
            print(f"Sesión existente: {session_id}. Usando contexto previo.")
        
        print(f"Solicitud F4 (Chat). Job: {job_id}. Sesión: {session_id}")
        
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        
        # Ejecuta el worker en el pool INDEPENDIENTE del chat
        executor_chat.submit(ejecutar_chatbot_sync, job_id, session_id, saved_paths, pregunta, es_nueva_sesion, temp_ingest_dir)
        
        return {"job_id": job_id, "status": "iniciado"}

    except Exception as e:
        print(f"Error endpoint fase 4: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase4/cerrar")
async def cerrar_sesion(session_id: str = Form(...)):
    """
    Elimina la sesión de la memoria RAM del servidor y limpia la carpeta de archivos físicos.
    """
    try:
        if session_id in CHAT_SESSIONS:
            # 1. Recuperamos la ruta de la carpeta ANTES de borrar la sesión de la RAM
            ruta_datos_sesion = CHAT_SESSIONS[session_id].get("ruta_datos")
            
            # 2. Eliminamos la carpeta física con todos sus JSON usando shutil
            if ruta_datos_sesion and ruta_datos_sesion.exists():
                shutil.rmtree(ruta_datos_sesion)
                print(f"Limpieza: Carpeta de sesión eliminada del disco ({ruta_datos_sesion.name})")

            # 3. Borramos la sesión de la memoria RAM
            del CHAT_SESSIONS[session_id]
            print(f"--- SESIÓN CERRADA: {session_id} (Memoria Liberada) ---")
            return {"status": "cerrado"}
        return {"status": "no_encontrado"}
    except Exception as e:
        print(f"Error cerrando sesión: {e}")
        return {"status": "error"}


@app.post("/fase10/iniciar_sesion")
async def fase10_iniciar_sesion(
    session_id: str = Form(...),
    cliente_id: int = Form(...),
    usuario_id: int = Form(...),
    nombre_usuario: str = Form(...),
    rol: str = Form(...),
    canales: str = Form(...)
):
    """
    Inicia una sesion del chatbot SMS.
    Recibe los datos del contexto del usuario autenticado.
    canales debe ser un JSON string: '[{"id":1,"codigo":"SMS","nombre":"Mensajes SMS"}]'
    """
    try:
        try:
            canales_parsed = json.loads(canales)
        except Exception:
            canales_parsed = []
 
        datos_sesion = {
            "cliente_id": cliente_id,
            "usuario_id": usuario_id,
            "nombre_usuario": nombre_usuario,
            "rol": rol,
            "canales": canales_parsed
        }
 
        sms_ai.iniciar_sesion_chat(session_id, datos_sesion)
        return {"status": "sesion_iniciada", "session_id": session_id}
 
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
@app.post("/fase10/bienvenida")
async def fase10_bienvenida(session_id: str = Form(...)):
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}

        def ejecutar():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.bienvenida_chat(session_id)
                if "error" in resultado:
                    trabajos[job_id]["status"] = "error"
                    trabajos[job_id]["error"] = resultado["error"]
                else:
                    trabajos[job_id]["status"] = "completado"
                    trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar)
        return {"job_id": job_id, "status": "iniciado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
@app.post("/fase10/chat")
async def fase10_chat(
    session_id: str = Form(...),
    pregunta: str = Form(...)
):
    """
    Primera pasada del chatbot SMS.
    Si necesita_db=False en el resultado: la respuesta esta lista en "respuesta".
    Si necesita_db=True: el resultado contiene "queries" para ejecutar en PostgreSQL.
      En ese caso llamar a /fase10/chat_con_datos con los datos obtenidos.
    """
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}

        def ejecutar_chat():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.responder_chat(session_id, pregunta)
                trabajos[job_id]["status"] = "completado"
                trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar_chat)
        return {"job_id": job_id, "status": "iniciado"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10/chat_con_datos")
async def fase10_chat_con_datos(
    session_id: str = Form(...),
    pregunta: str = Form(...),
    datos: str = Form(...)
):
    """
    Segunda pasada del chatbot SMS cuando necesita_db=True.
    Recibe los datos ejecutados en PostgreSQL y genera la respuesta final.
    datos: JSON string con los resultados de los queries.
    """
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}

        try:
            datos_parsed = json.loads(datos)
        except Exception:
            raise HTTPException(status_code=400, detail="El campo datos debe ser un JSON valido")

        def ejecutar_chat_con_datos():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.responder_chat_con_datos(session_id, pregunta, datos_parsed)
                trabajos[job_id]["status"] = "completado"
                trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar_chat_con_datos)
        return {"job_id": job_id, "status": "iniciado"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
@app.post("/fase10/cerrar_sesion")
async def fase10_cerrar_sesion(session_id: str = Form(...)):
    """
    Cierra la sesion del chatbot y libera la memoria.
    """
    try:
        sms_ai.cerrar_sesion_chat(session_id)
        return {"status": "sesion_cerrada", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
# =============================================================================
# FASES 10.1 A 10.4 - INSIGHTS DIARIOS
# Flujo de uso:
# 1. POST /fase10_x/solicitar_sql    -> Gemini genera el SQL, devuelve job_id + sql
# 2. Backend externo ejecuta el SQL en PostgreSQL
# 3. POST /fase10_x/enviar_resultado -> Se mandan los datos, Gemini genera insights
# 4. GET  /estado/{job_id}           -> Consultar estado (reutiliza endpoint existente)
# =============================================================================
 
@app.post("/fase10_1/solicitar_sql")
async def fase10_1_solicitar_sql(
    cliente_id: int = Form(...),
    fecha_corte: str = Form(...)
):
    """
    Fase 10.1 - Distribucion Geografica. Paso 1.
    Devuelve el SQL listo para ejecutar en PostgreSQL.
    Respuesta inmediata, sin polling.
    """
    try:
        resultado = sms_ai.solicitar_sql_insight("10_1", cliente_id, fecha_corte)
        if "error" in resultado:
            raise HTTPException(status_code=500, detail=resultado["error"])
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_1/enviar_resultado")
async def fase10_1_enviar_resultado(
    clave: str = Form(...),
    datos: str = Form(...)
):
    """
    Fase 10.1 - Distribucion Geografica. Paso 2.
    Recibe el JSON crudo que devolvio PostgreSQL y genera los insights.
    clave: el valor devuelto por solicitar_sql en el campo "clave".
    datos: JSON string tal cual como lo devuelve la base de datos.
    """
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        try:
            datos_parsed = json.loads(datos)
        except Exception:
            raise HTTPException(status_code=400, detail="El campo datos debe ser un JSON valido")

        def ejecutar():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.procesar_resultado_insight(clave, datos_parsed)
                trabajos[job_id]["status"] = "completado"
                trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar)
        return {"job_id": job_id, "status": "iniciado"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_2/solicitar_sql")
async def fase10_2_solicitar_sql(
    cliente_id: int = Form(...),
    fecha_corte: str = Form(...)
):
    """
    Fase 10.2 - Rentabilidad y Finanzas. Paso 1.
    Devuelve el SQL listo para ejecutar en PostgreSQL.
    """
    try:
        resultado = sms_ai.solicitar_sql_insight("10_2", cliente_id, fecha_corte)
        if "error" in resultado:
            raise HTTPException(status_code=500, detail=resultado["error"])
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_2/enviar_resultado")
async def fase10_2_enviar_resultado(
    clave: str = Form(...),
    datos: str = Form(...)
):
    """
    Fase 10.2 - Rentabilidad y Finanzas. Paso 2.
    Recibe el JSON crudo de PostgreSQL y genera el reporte financiero.
    """
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        try:
            datos_parsed = json.loads(datos)
        except Exception:
            raise HTTPException(status_code=400, detail="El campo datos debe ser un JSON valido")

        def ejecutar():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.procesar_resultado_insight(clave, datos_parsed)
                trabajos[job_id]["status"] = "completado"
                trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar)
        return {"job_id": job_id, "status": "iniciado"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_3/solicitar_sql")
async def fase10_3_solicitar_sql(
    cliente_id: int = Form(...),
    fecha_corte: str = Form(...)
):
    """
    Fase 10.3 - Consumo por Usuarios. Paso 1.
    Devuelve el SQL listo para ejecutar en PostgreSQL.
    """
    try:
        resultado = sms_ai.solicitar_sql_insight("10_3", cliente_id, fecha_corte)
        if "error" in resultado:
            raise HTTPException(status_code=500, detail=resultado["error"])
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_3/enviar_resultado")
async def fase10_3_enviar_resultado(
    clave: str = Form(...),
    datos: str = Form(...)
):
    """
    Fase 10.3 - Consumo por Usuarios. Paso 2.
    Recibe el JSON crudo de PostgreSQL y genera el reporte de consumo.
    """
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        try:
            datos_parsed = json.loads(datos)
        except Exception:
            raise HTTPException(status_code=400, detail="El campo datos debe ser un JSON valido")

        def ejecutar():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.procesar_resultado_insight(clave, datos_parsed)
                trabajos[job_id]["status"] = "completado"
                trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar)
        return {"job_id": job_id, "status": "iniciado"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_4/solicitar_sql")
async def fase10_4_solicitar_sql(
    cliente_id: int = Form(...),
    fecha_corte: str = Form(...)
):
    """
    Fase 10.4 - Seguridad y Alertas. Paso 1.
    Devuelve el SQL listo para ejecutar en PostgreSQL.
    """
    try:
        resultado = sms_ai.solicitar_sql_insight("10_4", cliente_id, fecha_corte)
        if "error" in resultado:
            raise HTTPException(status_code=500, detail=resultado["error"])
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fase10_4/enviar_resultado")
async def fase10_4_enviar_resultado(
    clave: str = Form(...),
    datos: str = Form(...)
):
    """
    Fase 10.4 - Seguridad y Alertas. Paso 2.
    Recibe el JSON crudo de PostgreSQL y genera el reporte de seguridad.
    """
    try:
        job_id = str(uuid.uuid4())
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        try:
            datos_parsed = json.loads(datos)
        except Exception:
            raise HTTPException(status_code=400, detail="El campo datos debe ser un JSON valido")

        def ejecutar():
            try:
                trabajos[job_id]["status"] = "procesando"
                resultado = sms_ai.procesar_resultado_insight(clave, datos_parsed)
                trabajos[job_id]["status"] = "completado"
                trabajos[job_id]["resultado"] = resultado
            except Exception as e:
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = str(e)

        executor_chat.submit(ejecutar)
        return {"job_id": job_id, "status": "iniciado"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)