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
    try:
        print(f"\n--- [WORKER] INICIANDO FASE 1 (IA): {pdf_path.name} ---")
        
        # Ejecuta la limpieza del directorio de salida
        limpiar_directorio(EXT_OUTPUT)
        
        # Ejecuta el extractor cambiando el directorio de trabajo temporalmente
        original_cwd = os.getcwd()
        try:
            os.chdir(DIR_EXTRACTOR)
            extractor_ia.main_extraction_ia(str(pdf_path), str(EXT_OUTPUT))
        except Exception as e:
            print(f"Error critico en extractor IA: {e}")
            raise e
        finally:
            os.chdir(original_cwd)
        
        # Recopila los resultados generados
        resultados = {}
        archivos_generados = list(EXT_OUTPUT.glob("*.json"))
        
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

def ejecutar_chatbot_sync(job_id: str, session_id: str, archivos_temp_paths: List[Path], pregunta_usuario: str, es_nueva_sesion: bool):
    """
    Ejecuta la lógica del Chatbot (Fase 4) gestionando memoria de sesión.
    """
    try:
        print(f"\n--- [WORKER] CHATBOT | SESIÓN: {session_id} ---")
        trabajos[job_id]["status"] = "procesando"

        # 1. GESTIONA CARPETAS Y ARCHIVOS
        if es_nueva_sesion:
            nombre_empresa_raw = None
            # Busca en los archivos temporales para extraer el nombre
            for temp_path in archivos_temp_paths:
                if "_DATOS" in temp_path.name.upper():
                    try:
                        with open(temp_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            nombre_empresa_raw = data.get("Nombre de la empresa del estado de cuenta")
                            if nombre_empresa_raw: break
                    except Exception: pass

            if nombre_empresa_raw:
                nombre_carpeta = nombre_empresa_raw.strip().replace(" ", "_")
            else:
                nombre_carpeta = "EMPRESA_DESCONOCIDA_" + str(uuid.uuid4())[:8]

            dir_empresa = CHATBOT_INPUT_BASE / nombre_carpeta
            dir_empresa.mkdir(parents=True, exist_ok=True)

            # Mueve los archivos a la carpeta definitiva
            for temp_path in archivos_temp_paths:
                dest = dir_empresa / temp_path.name
                shutil.move(str(temp_path), str(dest))
            
            # --- EJECUTA LIMPIEZA SEGURA: Borra carpeta TEMP_INGEST vacía ---
            try:
                # Obtiene la carpeta padre de los archivos temporales
                carpeta_temporal = archivos_temp_paths[0].parent
                
                # Elimina solo si el nombre contiene "TEMP_INGEST"
                if "TEMP_INGEST" in carpeta_temporal.name and carpeta_temporal.exists():
                    shutil.rmtree(carpeta_temporal)
                    print(f"Limpieza: Carpeta temporal eliminada ({carpeta_temporal.name})")
            except Exception as e:
                print(f"Nota: No se pudo borrar carpeta temp (no afecta al sistema): {e}")
            # ---------------------------------------------------------------

            # Guarda la ruta en la sesión
            CHAT_SESSIONS[session_id]["ruta_datos"] = dir_empresa
            print(f"Contexto guardado para sesión {session_id} en: {dir_empresa}")

        # 2. RECUPERA EL CONTEXTO DE LA SESIÓN
        dir_datos_sesion = CHAT_SESSIONS[session_id].get("ruta_datos")
        historial_actual = CHAT_SESSIONS[session_id]["historial"]

        if not dir_datos_sesion or not dir_datos_sesion.exists():
            raise Exception("Error de sesión: No se encontraron los datos cargados.")

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

@app.post("/fase4/chat")
async def chatbot_consultar(
    files: List[UploadFile] = File(None), # Ahora es opcional (None)
    pregunta: str = Form(...),
    session_id: str = Form(...) # Recibimos el ID de sesión
):
    """
    Endpoint Chatbot con soporte de SESIONES.
    """
    try:
        job_id = str(uuid.uuid4())
        es_nueva_sesion = False
        saved_paths = []

        # Verifica si la sesión existe en memoria
        if session_id not in CHAT_SESSIONS:
            print(f"Nueva sesión detectada: {session_id}")
            CHAT_SESSIONS[session_id] = {"historial": [], "ruta_datos": None}
            es_nueva_sesion = True
            
            # Si es nueva, requiere subir archivos
            if not files:
                 raise HTTPException(status_code=400, detail="Nueva sesión requiere subir archivos.")

            # Guarda archivos temporalmente para que el worker los procese
            temp_ingest_dir = CHATBOT_INPUT_BASE / f"TEMP_INGEST_{job_id}"
            temp_ingest_dir.mkdir(parents=True, exist_ok=True)
            
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
        executor_chat.submit(ejecutar_chatbot_sync, job_id, session_id, saved_paths, pregunta, es_nueva_sesion)
        
        return {"job_id": job_id, "status": "iniciado"}

    except Exception as e:
        print(f"Error endpoint fase 4: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/fase4/cerrar")
async def cerrar_sesion(session_id: str = Form(...)):
    """
    Elimina la sesión de la memoria RAM del servidor.
    """
    try:
        if session_id in CHAT_SESSIONS:
            del CHAT_SESSIONS[session_id]
            print(f"--- SESIÓN CERRADA: {session_id} (Memoria Liberada) ---")
            return {"status": "cerrado"}
        return {"status": "no_encontrado"}
    except Exception as e:
        print(f"Error cerrando sesión: {e}")
        return {"status": "error"}
# -----------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)