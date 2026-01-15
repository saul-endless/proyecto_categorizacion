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
gpu_lock = asyncio.Lock()

# Se almacenan los trabajos en memoria
trabajos = {}
executor = ThreadPoolExecutor(max_workers=2)

# Funciones auxiliares

def limpiar_directorio(directorio: Path):
    # Se eliminan los archivos contenidos dentro del directorio especificado
    if directorio.exists():
        for archivo in directorio.glob("*"):
            if archivo.is_file():
                archivo.unlink()

def cargar_modulo_con_espacios(ruta_archivo, nombre_modulo):
    # Se carga dinamicamente un modulo de Python desde una ruta especifica
    spec = importlib.util.spec_from_file_location(nombre_modulo, ruta_archivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre_modulo] = modulo
    spec.loader.exec_module(modulo)
    return modulo

def obtener_ultimo_archivo(directorio, patron="*"):
    # Se busca y retorna el archivo mas reciente que coincida con el patron
    archivos = list(directorio.glob(patron))
    return max(archivos, key=os.path.getctime) if archivos else None

# Endpoints

@app.get("/")
def home():
    # Se retorna el estado operativo del servidor
    return {"estado": "API Activa", "gpu": "RTX 5090 Lista"}

@app.get("/estado/{job_id}")
async def obtener_estado(job_id: str):
    # Se consulta el estado de un trabajo en ejecucion
    if job_id not in trabajos:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    return trabajos[job_id]

@app.post("/fase1/extraer")
async def extraer_pdf(file: UploadFile = File(...)):
    try:
        print(f"\n--- INICIANDO FASE 1: {file.filename} ---")
        # Se limpian los directorios de entrada y salida del proceso extractor
        limpiar_directorio(EXT_INPUT)
        limpiar_directorio(EXT_OUTPUT)
        
        # Se guarda el archivo PDF recibido en el servidor
        pdf_path = EXT_INPUT / file.filename
        with open(pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        if str(DIR_EXTRACTOR) not in sys.path:
            sys.path.append(str(DIR_EXTRACTOR))
            
        original_cwd = os.getcwd()
        try:
            # Se ejecuta el script principal del extractor externo
            os.chdir(DIR_EXTRACTOR)
            import main_extractor # type: ignore
            main_extractor.main() 
        except Exception as e:
            print(f"Error critico en extractor: {e}")
            raise HTTPException(status_code=500, detail=f"Error interno extractor: {str(e)}")
        finally:
            os.chdir(original_cwd)
            
        resultados = {}
        # Se procesan y trasladan los archivos JSON generados
        for json_file in EXT_OUTPUT.glob("*.json"):
            dest = ORQ_OUTPUT / json_file.name
            shutil.copy(json_file, dest)
            
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
                
        if not resultados:
            raise HTTPException(status_code=400, detail="El extractor no generó archivos JSON.")
            
        print("--- FASE 1 COMPLETADA ---")
        return resultados

    except Exception as e:
        print(f"Error general Fase 1: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def ejecutar_categorizacion_sync(job_id: str, archivos_guardados: list):
    # Se ejecuta la categorizacion de manera sincrona en un hilo separado
    try:
        print("\n--- INICIANDO FASE 2: CATEGORIZACION ---")
        trabajos[job_id]["status"] = "procesando"
        
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
        
        if 'CUDA_VISIBLE_DEVICES' in os.environ: del os.environ['CUDA_VISIBLE_DEVICES']
        
        original_cwd = os.getcwd()
        os.chdir(DIR_MODELOS)
        
        try:
            print("Cargando modelo en RTX 5090...")
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

            print("--- FASE 2 COMPLETADA ---")
            trabajos[job_id]["status"] = "completado"
            trabajos[job_id]["resultado"] = resultados_categorizados

        finally:
            if 'llm' in locals():
                del llm
            gc.collect()
            os.chdir(original_cwd)

    except Exception as e:
        print(f"Error Fase 2: {e}")
        import traceback
        traceback.print_exc()
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)

@app.post("/fase2/categorizar")
async def categorizar_transacciones(files: List[UploadFile] = File(...)):
    # Se asegura el uso exclusivo de la GPU mediante el bloqueo
    async with gpu_lock:
        try:
            # Se limpian los directorios de trabajo del modelo
            limpiar_directorio(MOD_INPUT)
            limpiar_directorio(MOD_OUTPUT)
            
            nombres_procesados = []
            
            # Se guardan los archivos recibidos para su procesamiento
            for file in files:
                dest_path = MOD_INPUT / file.filename
                content = await file.read()
                with open(dest_path, "wb") as buffer:
                    buffer.write(content)
                nombres_procesados.append(file.filename)

            # Se genera un identificador unico para el trabajo
            job_id = str(uuid.uuid4())
            trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
            
            # Se ejecuta el procesamiento en un hilo separado
            executor.submit(ejecutar_categorizacion_sync, job_id, nombres_procesados)
            
            return {"job_id": job_id, "status": "iniciado"}

        except Exception as e:
            print(f"Error Fase 2: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(e))

def ejecutar_perfilado_sync(job_id: str, nombre_real_datos: str):
    # Se ejecuta el perfilado de manera sincrona en un hilo separado
    try:
        print("\n--- EJECUTANDO PERFILADO ---")
        trabajos[job_id]["status"] = "procesando"
        
        ruta_script = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
        mod = cargar_modulo_con_espacios(ruta_script, "modulo_modelos")
        
        original_cwd = os.getcwd()
        os.chdir(DIR_MODELOS)
        
        try:
            print("Cargando modelo de perfilado...")
            llm = mod.cargar_modelo(mod.RUTA_MODELO_8B)
            
            # Se inicia el procesamiento utilizando el nombre estandarizado
            mod.procesar_perfilado_empresarial(llm, "TEMP_EXEC_DATOS.json")
            
            # Se verifica la generacion del resultado
            out_path = MOD_OUTPUT / "TEMP_EXEC_DATOS.json"
            
            if out_path.exists():
                # Se restaura el nombre original complejo para la entrega al usuario
                nombre_final = Path(nombre_real_datos).stem + "_PERFIL.json"
                dest_final = ORQ_OUTPUT / nombre_final
                
                shutil.copy(out_path, dest_final)
                
                with open(dest_final, "r", encoding="utf-8") as f:
                    print("--- FASE 3 COMPLETADA ---")
                    trabajos[job_id]["status"] = "completado"
                    trabajos[job_id]["resultado"] = json.load(f)
            else:
                print("Error: No se genero archivo de perfil")
                trabajos[job_id]["status"] = "error"
                trabajos[job_id]["error"] = "El modelo no generó el perfil."

        finally:
            if 'llm' in locals():
                del llm
            gc.collect()
            os.chdir(original_cwd)

    except Exception as e:
        print(f"Error Fase 3: {e}")
        trabajos[job_id]["status"] = "error"
        trabajos[job_id]["error"] = str(e)

@app.post("/fase3/perfilar")
async def perfilar_empresa(files: List[UploadFile] = File(...)):
    # Se asegura el uso exclusivo de la GPU mediante el bloqueo
    async with gpu_lock:
        try:
            print("\n--- INICIANDO FASE 3: PERFILADO ---")
            limpiar_directorio(MOD_INPUT)
            limpiar_directorio(MOD_OUTPUT)

            nombre_real_datos = None
            
            # Se normalizan los nombres de archivo internamente para estandarizar el procesamiento
            # Se evita el conflicto con nombres de archivo complejos o multiples sufijos
            for file in files:
                upper_name = file.filename.upper()
                nombre_temp = file.filename
                
                if "_DATOS" in upper_name:
                    nombre_real_datos = file.filename
                    nombre_temp = "TEMP_EXEC_DATOS.json"
                elif "_INGRESOS" in upper_name:
                    # Se asigna el nombre esperado por el script del modelo
                    nombre_temp = "TEMP_EXEC_INGRESOS_CON_GIROS.json"
                elif "_EGRESOS" in upper_name:
                    # Se asigna el nombre esperado por el script del modelo
                    nombre_temp = "TEMP_EXEC_EGRESOS_CON_GIROS.json"
                
                dest_path = MOD_INPUT / nombre_temp
                content = await file.read()
                with open(dest_path, "wb") as buffer:
                    buffer.write(content)

            if not nombre_real_datos:
                print("Error: Falta archivo _DATOS")
                raise HTTPException(status_code=400, detail="Falta el archivo _DATOS en la petición")

            # Se genera un identificador unico para el trabajo
            job_id = str(uuid.uuid4())
            trabajos[job_id] = {"status": "iniciado", "resultado": None, "error": None}
            
            # Se ejecuta el procesamiento en un hilo separado
            executor.submit(ejecutar_perfilado_sync, job_id, nombre_real_datos)
            
            return {"job_id": job_id, "status": "iniciado"}

        except Exception as e:
            print(f"Error Fase 3: {e}")
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Se inicia el servidor en el puerto especificado
    uvicorn.run(app, host="0.0.0.0", port=8000)