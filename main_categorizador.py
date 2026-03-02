# Se importan las librerias necesarias para manejo de sistema, archivos y modelo
import os
import sys
import json
from pathlib import Path

# Intentamos importar la libreria del modelo
try:
    from llama_cpp import Llama 
except ImportError:
    print("Error critico: La libreria llama_cpp no esta instalada.")
    print("Ejecuta: pip install llama-cpp-python")
    sys.exit(1)

# Se definen las rutas base del sistema (siguiendo la estructura del orquestador)
BASE_DIR = Path("/home/endless/FUNCIONALIDADES")
DIR_EXTRACTOR = BASE_DIR / "PROYECTO EXTRACTOR"
DIR_MODELOS = BASE_DIR / "PRUEBA MODELOS"

# Rutas especificas de entrada de datos y ubicacion del modelo
RUTA_DATOS_JSON = DIR_EXTRACTOR / "output"
RUTA_MODELO_LLM = DIR_MODELOS / "MODELOS/Llama-3.3-70B-Instruct-GGUF"

# Configuracion del sistema
N_CTX = 8192  # Ventana de contexto amplia para leer varios archivos
N_GPU_LAYERS = -1  # Usa toda la GPU disponible

def cargar_contexto_financiero(directorio_datos):
    # Se busca y consolida la informacion de todos los archivos json en la ruta
    if not directorio_datos.exists():
        print(f"Advertencia: La ruta {directorio_datos} no existe.")
        return ""

    archivos_json = list(directorio_datos.glob("*.json"))
    
    if not archivos_json:
        print("Aviso: No se encontraron archivos JSON en la carpeta de output.")
        return ""

    print(f"Procesando {len(archivos_json)} archivos de datos financieros...")
    
    texto_consolidado = ""
    
    for archivo in archivos_json:
        try:
            with open(archivo, 'r', encoding='utf-8') as f:
                datos = json.load(f)
                nombre_archivo = archivo.name
                
                # Se etiqueta el inicio de cada archivo para que el modelo sepa la fuente
                texto_consolidado += f"\n--- INFORMACION DEL ARCHIVO: {nombre_archivo} ---\n"
                texto_consolidado += json.dumps(datos, indent=2, ensure_ascii=False)
                texto_consolidado += "\n"
        except Exception as e:
            print(f"Error al leer el archivo {archivo.name}: {e}")

    return texto_consolidado

def cargar_modelo_local(ruta_modelo):
    # Se inicializa el modelo LLM desde la ruta local especificada
    if not ruta_modelo.exists():
        # A veces la ruta puede ser un archivo directo sin extension visible o con ella
        # Verificamos si existe como archivo string
        if not os.path.exists(str(ruta_modelo)):
            print(f"Error critico: No se encuentra el archivo del modelo en: {ruta_modelo}")
            return None

    print(f"Cargando modelo desde: {ruta_modelo}")
    print("Espere un momento, cargando en memoria...")

    try:
        llm = Llama(
            model_path=str(ruta_modelo),
            n_ctx=N_CTX,
            n_gpu_layers=N_GPU_LAYERS,
            verbose=False # Se desactiva el log tecnico para mantener la consola limpia
        )
        return llm
    except Exception as e:
        print(f"Error fatal al cargar el modelo: {e}")
        return None

def construir_prompt_sistema():
    # Se define la personalidad estricta del asesor financiero
    prompt = """
    Eres un asesor financiero personal experto.
    
    TUS REGLAS DE RESPUESTA:
    1. LENGUAJE SENCILLO: Explica conceptos complejos usando analogias de la vida diaria (ejemplo: compara una deuda con una mochila pesada, o el flujo de efectivo con el agua en un tanque).
    2. DATOS CONCRETOS: Debes usar OBLIGATORIAMENTE los numeros que aparecen en el contexto. No digas "gastaste mucho", di "gastaste 5,000 pesos".
    3. CERO TECNICISMOS: No uses palabras como "EBITDA", "Rendimiento Anualizado" o "Pasivo Circulante" sin explicarlas con palabras de niño de 10 años.
    4. FONDO DE VERDAD: Tu respuesta se basa exclusivamente en los archivos JSON proporcionados. Si no hay datos, dilo claramente.
    """
    return prompt

def consultar_modelo(llm, contexto, pregunta_usuario):
    # Se envia la consulta al modelo integrando el contexto de los archivos
    
    sistema = construir_prompt_sistema()
    
    # Se inyecta el contexto de los archivos dentro del mensaje del usuario
    # para asegurar que el modelo lo tenga presente en esta interaccion
    prompt_usuario_completo = f"""
    CONTEXTO DE LOS DATOS FINANCIEROS (ARCHIVOS JSON):
    {contexto}

    PREGUNTA DEL CLIENTE:
    {pregunta_usuario}
    """

    mensajes = [
        {"role": "system", "content": sistema},
        {"role": "user", "content": prompt_usuario_completo}
    ]

    respuesta = llm.create_chat_completion(
        messages=mensajes,
        temperature=0.7, # Creatividad balanceada para analogias
        max_tokens=1000
    )

    return respuesta['choices'][0]['message']['content']

def main():
    # Se verifica que existan las carpetas necesarias
    if not DIR_EXTRACTOR.exists():
        print("Error: No se encuentra el directorio del proyecto extractor.")
        return

    print("--- INICIANDO SISTEMA DE CHATBOT FINANCIERO ---")

    # 1. Carga de datos
    print("\n>>> FASE 1: CARGA DE CONTEXTO")
    contexto_datos = cargar_contexto_financiero(RUTA_DATOS_JSON)
    
    if not contexto_datos:
        print("Advertencia: Se iniciara el chat sin datos financieros cargados.")
    else:
        print("Datos financieros cargados correctamente en memoria.")

    # 2. Carga del modelo
    print("\n>>> FASE 2: INICIALIZACION DEL MODELO (GPU)")
    llm = cargar_modelo_local(RUTA_MODELO_LLM)
    
    if not llm:
        print("No se pudo iniciar el sistema. Terminando programa.")
        return

    print("\n" + "="*50)
    print("ASESOR FINANCIERO LISTO")
    print("Escribe 'salir' para terminar la sesion.")
    print("="*50 + "\n")

    # 3. Bucle de interaccion
    while True:
        try:
            pregunta = input("Usuario: ")
            
            if pregunta.lower() in ['salir', 'exit', 'bye']:
                print("Cerrando sesion...")
                break
            
            if not pregunta.strip():
                continue

            print("\nAnalizando...\n")
            
            respuesta = consultar_modelo(llm, contexto_datos, pregunta)
            
            print(f"Asesor: {respuesta}\n")
            print("-" * 50)

        except KeyboardInterrupt:
            print("\nSesion interrumpida por el usuario.")
            break
        except Exception as e:
            print(f"Ocurrio un error inesperado: {e}")

    # Limpieza de memoria al salir
    del llm

if __name__ == "__main__":
    main()