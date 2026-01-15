from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import shutil
import os
import sys
import json
import importlib.util
import gc
from pathlib import Path
from typing import List
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

# Se definen las rutas base del sistema
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

# Se crean los directorios necesarios si no existen
for d in [ORQ_INPUT, ORQ_OUTPUT, EXT_INPUT, EXT_OUTPUT, MOD_INPUT, MOD_OUTPUT]:
    d.mkdir(parents=True, exist_ok=True)

# Se inicializa la aplicacion FastAPI
app = FastAPI(title="API Orquestador Financiero")

# Se inicializa el bloqueo para la gestion de la GPU
# Nota: Usamos un bloqueo global, pero dentro de los threads usaremos lógica síncrona
gpu_sem = asyncio.Semaphore(1) 

# Se almacenan los trabajos en memoria
trabajos = {}
executor = ThreadPoolExecutor(max_workers=1) # 1 Worker para asegurar que no choquen en VRAM

# Importacion del extractor IA
sys.path.append(str(DIR_EXTRACTOR))
try:
    import extractor_ia # type: ignore
except ImportError:
    print("Error CRITICO: No se encontro el modulo extractor_ia.py")

# Funciones auxiliares

def limpiar_directorio(directorio: Path):
    if directorio.exists():
        for archivo in directorio.glob("*"):
            if archivo.is_file():
                archivo.unlink()

def cargar_modulo_con_espacios(ruta_archivo, nombre_modulo):
    spec = importlib.util.spec_from_file_location(nombre_modulo, ruta_archivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre_modulo] = modulo
    spec.loader.exec_module(modulo)
    return modulo

def obtener_ultimo_archivo(directorio, patron="*"):
    archivos = list(directorio.glob(patron))
    return max(archivos, key=os.path.getctime) if archivos else None

# --- LÓGICA DE EJECUCIÓN EN FONDO (WORKERS) ---

def ejecutar_extraccion_sync(job_id: str, pdf_path: Path):
    """
    Worker para la Fase 1 (Extractor Qwen). Corre en hilo separado.
    """
    trabajos[job_id]["status"] = "procesando"
    try:
        print(f"\n--- [WORKER] INICIANDO FASE 1 (IA): {pdf_path.name} ---")
        
        # Limpieza previa
        limpiar_directorio(EXT_OUTPUT)
        
        # Ejecución del Extractor
        original_cwd = os.getcwd()
        try:
            os.chdir(DIR_EXTRACTOR)
            extractor_ia.main_extraction_ia(str(pdf_path), str(EXT_OUTPUT))
        except Exception as e:
            print(f"Error critico en extractor IA: {e}")
            raise e
        finally:
            os.chdir(original_cwd)
        
        # Recolección de resultados
        resultados = {}
        archivos_generados = list(EXT_OUTPUT.glob("*.json"))
        
        if not archivos_generados:
            raise Exception("El extractor IA no generó archivos JSON.")

        for json_file in archivos_generados:
            # Copia al output del orquestador
            dest = ORQ_OUTPUT / json_file.name
            shutil.copy(json_file, dest)
            
            # Carga para respuesta
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
        
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
        
        if 'CUDA_VISIBLE_DEVICES' in os.environ: del os.environ['CUDA_VISIBLE_DEVICES']
        
        original_cwd = os.getcwd()
        os.chdir(DIR_MODELOS)
        
        try:
            print("Cargando modelo Llama en RTX 5090...")
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
            resultados_categorizados = {}

            for nombre_archivo in archivos_guardados:
                if "_INGRESOS" in nombre_archivo.upper() or "_EGRESOS" in nombre_archivo.upper():
                    print(f"Procesando {nombre_archivo}...")
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
        
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
        
        original_cwd = os.getcwd()
        os.chdir(DIR_MODELOS)
        
        try:
            print("Cargando modelo de perfilado...")
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
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
            if 'llm' in locals(): del llm
            gc.collect()
            os.chdir(original_cwd)

    except Exception as e:
        print(f"Error Fase 3: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)

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
    """
    Inicia la extracción visual (Qwen) de forma ASÍNCRONA.
    Devuelve un job_id inmediatamente para evitar timeouts.
    """
    try:
        # Generar ID
        job_id = str(uuid.uuid4())
        
        # Guardar PDF
        limpiar_directorio(EXT_INPUT)
        pdf_path = EXT_INPUT / file.filename
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        print(f"Solicitud recibida. Job ID: {job_id}. Archivo: {file.filename}")
        
        # Inicializar estado
        trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
        
        # Lanzar al pool de hilos (libera al servidor FastAPI para responder 200 OK ya)
        executor.submit(ejecutar_extraccion_sync, job_id, pdf_path)
        
        return {"job_id": job_id, "status": "iniciado", "mensaje": "Procesando en background"}

    except Exception as e:
        print(f"Error endpoint fase 1: {e}")
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
        
        executor.submit(ejecutar_categorizacion_sync, job_id, nombres_procesados)
        
        return {"job_id": job_id, "status": "iniciado"}

    except Exception as e:
        print(f"Error Fase 2: {e}")
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
        
        executor.submit(ejecutar_perfilado_sync, job_id, nombre_real_datos)
        
        return {"job_id": job_id, "status": "iniciado"}

    except Exception as e:
        print(f"Error Fase 3: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)