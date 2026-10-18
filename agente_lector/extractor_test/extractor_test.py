import json
import time
from pathlib import Path
import pypdfium2 as pdfium
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

def tiene_texto_nativo(pdf_path: Path, umbral: int = 100) -> bool:
    """Evalúa en milisegundos si un PDF tiene texto digital."""
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        if len(pdf) == 0:
            return False
            
        page = pdf.get_page(0)
        texto_extraido = page.get_textpage().get_text_bounded()
        caracteres_validos = len(texto_extraido.replace(" ", "").replace("\n", ""))
        
        page.close()
        pdf.close()
        
        print(f"-> [Pre-evaluación PDF] Caracteres válidos encontrados: {caracteres_validos}")
        return caracteres_validos >= umbral
    except Exception as e:
        print(f"Error al evaluar el PDF: {e}")
        return False

def main():
    # Cambia este nombre para probar con un PDF o con una imagen (ej. 'foto_bl.jpg')
    archivo_path = Path("documento_prueba.pdf")

    if not archivo_path.exists():
        print(f"❌ Error: No se encontró '{archivo_path.name}'.")
        return

    extension = archivo_path.suffix.lower()
    usar_ocr = False

    print(f"Iniciando evaluación de '{archivo_path.name}'...")
    start_time = time.time()

    # 1. DILUCIDAR EL CAMINO: ¿Es imagen o PDF? ¿Necesita OCR?
    if extension in [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"]:
        print("📸 Imagen detectada. El motor OCR es obligatorio.")
        usar_ocr = True
    elif extension == ".pdf":
        print("📄 Documento PDF detectado. Verificando si es digital o escaneado...")
        # Invertimos el resultado: si TIENE texto nativo, NO usamos OCR.
        usar_ocr = not tiene_texto_nativo(archivo_path)
        if usar_ocr:
            print("⚠️ Es un PDF escaneado. Se activará el motor OCR.")
        else:
            print("✅ Es un PDF digital puro. Extracción semántica ultrarrápida (OCR apagado).")
    else:
        print(f"❌ Formato no soportado: {extension}")
        return

    # 2. CONFIGURAR DOCLING SEGÚN LA DECISIÓN
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_table_structure = True 
    pipeline_options.do_ocr = usar_ocr

    if usar_ocr:
        pipeline_options.ocr_options = EasyOcrOptions(lang=["en", "es"])

    # Inicializar el convertidor
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            # Docling maneja las imágenes nativamente cuando se le pasa el archivo, 
            # pero usa las opciones base de OCR definidas arriba.
        }
    )

    # 3. PROCESAR EL DOCUMENTO
    print("\nProcesando documento...")
    conv_result = converter.convert(archivo_path)
    
    duration = time.time() - start_time
    print(f"Extracción finalizada en {duration:.2f} segundos.\n")

    # 4. EXPORTAR A JSON ESTRUCTURADO
    # Convertimos el documento de Docling a un diccionario de Python
    dict_output = conv_result.document.export_to_dict()
    
    # Serializamos el diccionario a formato JSON con sangría (indent=2) para que sea legible
    json_output = json.dumps(dict_output, indent=2, ensure_ascii=False)
    
    # 5. GUARDAR Y MOSTRAR RESULTADO
    output_file = Path("resultado_extraccion.json")
    output_file.write_text(json_output, encoding="utf-8")
    print(f"🎉 Resultado JSON guardado exitosamente en: {output_file.resolve()}")

    # Mostrar un pequeño extracto del JSON generado
    print("\n--- VISTA PREVIA DEL JSON (Primeras 20 líneas) ---")
    lineas_json = json_output.splitlines()
    for linea in lineas_json[:20]:
        print(linea)
    if len(lineas_json) > 20:
        print(f"... [{len(lineas_json) - 20} líneas más no mostradas]")

if __name__ == "__main__":
    main()