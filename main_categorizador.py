# Se importan las librerias necesarias para manejo de sistema y archivos
import os
import sys
import shutil
import time
import importlib.util
from pathlib import Path

# Se definen las rutas base de los proyectos
BASE_DIR = Path("/home/endless/FUNCIONALIDADES")
DIR_CATEGORIZACION = BASE_DIR / "PROYECTO CATEGORIZACION"
DIR_EXTRACTOR = BASE_DIR / "PROYECTO EXTRACTOR"
DIR_MODELOS = BASE_DIR / "PRUEBA MODELOS"

# Se definen las carpetas de input y output del orquestador
ORQ_INPUT = DIR_CATEGORIZACION / "input"
ORQ_OUTPUT = DIR_CATEGORIZACION / "output"

# Se definen las carpetas internas de los proyectos existentes
EXT_INPUT = DIR_EXTRACTOR / "input"
EXT_OUTPUT = DIR_EXTRACTOR / "output"
MOD_INPUT = DIR_MODELOS / "INPUT"
MOD_OUTPUT = DIR_MODELOS / "OUTPUT"

# Importacion del extractor IA
sys.path.append(str(DIR_EXTRACTOR))
try:
    import extractor_ia # type: ignore
except ImportError:
    print("Error: No se encontro el modulo extractor_ia.py en la ruta del extractor.")

def limpiar_directorio(directorio):
    # Se eliminan archivos previos en un directorio para evitar mezclas
    if directorio.exists():
        for archivo in directorio.glob("*"):
            if archivo.is_file():
                archivo.unlink()

def obtener_ultimo_archivo(directorio, patron="*"):
    # Se obtiene el archivo mas reciente en un directorio dado
    archivos = list(directorio.glob(patron))
    if not archivos:
        return None
    return max(archivos, key=os.path.getctime)

def cargar_modulo_con_espacios(ruta_archivo, nombre_modulo):
    # Se carga dinamicamente un modulo python cuyo nombre o ruta tiene espacios
    spec = importlib.util.spec_from_file_location(nombre_modulo, ruta_archivo)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre_modulo] = modulo
    spec.loader.exec_module(modulo)
    return modulo

def main():
    # Se verifica que existan las carpetas de trabajo
    if not ORQ_INPUT.exists() or not ORQ_OUTPUT.exists():
        print("Error: Asegurate de crear las carpetas input y output en PROYECTO CATEGORIZACION")
        return

    # Se busca el PDF en la carpeta de input del orquestador
    pdfs = list(ORQ_INPUT.glob("*.pdf"))
    if not pdfs:
        print("No se encontraron PDFs en la carpeta input de PROYECTO CATEGORIZACION.")
        return
    
    pdf_actual = pdfs[0]
    print(f"Procesando archivo: {pdf_actual.name}")

    # =========================================================
    # FASE 1: EXTRACCION DE DATOS (IA VISUAL - QWEN)
    # =========================================================
    print("\n>>> FASE 1: EXTRACCION VISUAL (GPU - QWEN)...")

    # Se limpian las carpetas del extractor para asegurar proceso limpio
    EXT_INPUT.mkdir(parents=True, exist_ok=True)
    EXT_OUTPUT.mkdir(parents=True, exist_ok=True)
    limpiar_directorio(EXT_INPUT)
    limpiar_directorio(EXT_OUTPUT)

    # Se copia el PDF al input del extractor
    ruta_pdf_destino = EXT_INPUT / pdf_actual.name
    shutil.copy(pdf_actual, ruta_pdf_destino)

    # Se ejecuta el extractor IA
    try:
        os.chdir(DIR_EXTRACTOR)
        # Llamada directa a la funcion main del extractor IA
        extractor_ia.main_extraction_ia(str(ruta_pdf_destino), str(EXT_OUTPUT))
    except Exception as e:
        print(f"Error durante la ejecucion del extractor IA: {e}")
        return

    # Se mueven los resultados JSON al output del orquestador
    archivos_generados = list(EXT_OUTPUT.glob("*.json"))
    rutas_jsons_procesar = []
    ruta_datos_json = None

    for json_file in archivos_generados:
        destino = ORQ_OUTPUT / json_file.name
        shutil.move(str(json_file), str(destino))
        print(f"Archivo generado movido: {json_file.name}")
        
        # Identificamos los archivos para las siguientes fases
        if "_INGRESOS.json" in json_file.name or "_EGRESOS.json" in json_file.name:
            rutas_jsons_procesar.append(destino)
        if "_DATOS.json" in json_file.name:
            ruta_datos_json = destino

    # ---------------------------------------------------------
    # GESTION GPU: Qwen libera memoria al terminar, Llama carga limpio
    # ---------------------------------------------------------
    
    # Preparacion entorno modelos
    MOD_INPUT.mkdir(parents=True, exist_ok=True)
    MOD_OUTPUT.mkdir(parents=True, exist_ok=True)
    
    ruta_script_modelos = DIR_MODELOS / "PROBAR DIVERSOS MODELOS 2.py"
    modulo_modelos = cargar_modulo_con_espacios(ruta_script_modelos, "modulo_modelos")

    # Configuración del modelo (Usamos 8B por defecto)
    ruta_modelo_a_usar = modulo_modelos.RUTA_MODELO_8B 
    print(f"Cargando modelo LLM desde: {ruta_modelo_a_usar}")
    os.chdir(DIR_MODELOS)
    
    llm = modulo_modelos.cargar_modelo(ruta_modelo_a_usar)
    if not llm:
        print("Error: No se pudo cargar el modelo LLM.")
        return

    # =========================================================
    # FASE 2: CATEGORIZACION DE TRANSACCIONES (GPU)
    # =========================================================
    print("\n>>> FASE 2: CATEGORIZACION (GPU)...")

    # Archivos que necesitaremos tener listos para la fase 3
    archivos_con_giros_generados = []

    for ruta_json in rutas_jsons_procesar:
        print(f"Categorizando archivo: {ruta_json.name}")

        # Se copia el archivo al INPUT del modelo
        destino_input_modelo = MOD_INPUT / ruta_json.name
        shutil.copy(ruta_json, destino_input_modelo)

        tiempo_inicio = time.time()

        # Ejecuta la logica del modelo pasando el nombre del archivo
        modulo_modelos.procesar_logica_fusion(llm, ruta_json.name)

        # Buscamos el output "ANALISIS_..."
        nombre_base_sin_ext = ruta_json.stem
        patron_analisis = f"ANALISIS_{nombre_base_sin_ext}_*.json"
        archivo_resultado = obtener_ultimo_archivo(MOD_OUTPUT, patron_analisis)
        
        if archivo_resultado and archivo_resultado.stat().st_mtime >= tiempo_inicio:
            nuevo_nombre = f"{nombre_base_sin_ext}_CON_GIROS.json".upper()
            ruta_destino_final = ORQ_OUTPUT / nuevo_nombre
            
            shutil.move(str(archivo_resultado), str(ruta_destino_final))
            archivos_con_giros_generados.append(ruta_destino_final)
            print(f"Categorizacion finalizada. Guardado en: {ruta_destino_final.name}")
        else:
            print(f"Error: No se genero el archivo de analisis para {ruta_json.name}")

    # =========================================================
    # FASE 3: PERFILADO EMPRESARIAL (GPU)
    # =========================================================
    if ruta_datos_json and len(archivos_con_giros_generados) >= 2:
        print("\n>>> FASE 3: PERFILADO EMPRESARIAL (GPU)...")
        
        # 1. Copiar _DATOS.json al input de modelos
        shutil.copy(ruta_datos_json, MOD_INPUT / ruta_datos_json.name)
        
        # 2. Copiar los archivos _CON_GIROS generados al input de modelos
        # (El script de perfilado los busca ahí para cruzarlos)
        for archivo_giro in archivos_con_giros_generados:
            shutil.copy(archivo_giro, MOD_INPUT / archivo_giro.name)

        print(f"Perfilando empresa basada en: {ruta_datos_json.name}")
        
        tiempo_inicio_perfil = time.time()
        
        # Ejecutamos el perfilado
        modulo_modelos.procesar_perfilado_empresarial(llm, ruta_datos_json.name)
        
        # El resultado se guarda en MOD_OUTPUT con el mismo nombre que la entrada (_DATOS.json)
        resultado_perfil = MOD_OUTPUT / ruta_datos_json.name
        
        if resultado_perfil.exists() and resultado_perfil.stat().st_mtime >= tiempo_inicio_perfil:
            nombre_final_perfil = ruta_datos_json.stem + "_CON_GIRO.json"
            nombre_final_perfil = nombre_final_perfil.upper() # Todo mayusculas
            
            ruta_destino_perfil = ORQ_OUTPUT / nombre_final_perfil
            shutil.move(str(resultado_perfil), str(ruta_destino_perfil))
            
            print(f"Perfilado finalizado. Guardado en: {ruta_destino_perfil.name}")
        else:
             print("Error: No se generó el archivo de perfilado correctamente.")

    else:
        print("\n[SKIP] Fase 3 omitida: Faltan archivos _DATOS o los archivos de giros.")

    # Limpieza final de memoria
    del llm
    import gc
    gc.collect()

    print("\n==============================================")
    print("PROCESO COMPLETO FINALIZADO EXITOSAMENTE.")
    print(f"Revisa la carpeta: {ORQ_OUTPUT}")
    print("==============================================")

if __name__ == "__main__":
    main()