# PortIA_Agentes
# Proyecto PortIA Agentes
# Integrantes
  - Ignacio Andrade
  - Airon Saez
  - Nicolas Soto

Este repositorio contiene los agentes IA y servicios de Python. Operan de manera asíncrona y desacoplada, comunicándose con el Core mediante colas de mensajes.


# Descripción
## ¿Que hace?
PortIA_Agentes proyecta 2 microservicios especializados en Python y un servicio aparte
  - Agente lector (Ingesta y Extraccion): Escucha eventos de nuevos documentos, descarga el PDF (Bill of Lading), y utiliza un modelo de Lenguaje Grande (LLM) mediante Prompt Engineering para devolver un JSON estrictamente tipado
  - Sistema: Sistema Python que rescatara ID y hará la conexión con el servicio externo.
  - Servicio Externo: Servicio que replicara llamados como si estuviera llamando un IdContainer en pagina de Terminal Internacional

## ¿Que problema resuelve?
Ataca directamente dos de los problemas más costosos y repetitivos del ecosistema naviero:
  - Formatos Mutantes y Error Humano: Cada naviera tiene un diseño de PDF distinto. Un humano cansado confunde un 0 con una O al transcribir, rastreando contenedores que no existen. El Agente Lector estandariza estos formatos irregulares con más del 95% de precisión en segundos.
  - a Carga Cognitiva del Monitoreo: Revisar manualmente el estado de 100 contenedores en distintas páginas web de terminales requiere cientos de clics diarios. El Agente Inspector automatiza este "refresh" constante y añade variables externas (clima) que el portal de un forwarder no te da.

# Requisitos e instalación local

## Requisitos

- **Python 3.11.x**, la misma versión que usa la imagen Docker (`python:3.11-slim`).
- ~4 GB de disco libre (wheels de PyTorch CPU + modelos de Docling/EasyOCR).

## Instalación

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` usa el índice CPU de PyTorch (`--extra-index-url https://download.pytorch.org/whl/cpu`), por lo que instala `torch`/`torchvision` con sufijo `+cpu`.


| Variable | Valor por defecto | Uso |
|---|---|---|
| `DOCLING_ARTIFACTS_PATH` | `.cache/docling` | Caché de modelos de Docling |
| `HF_HOME` | `.cache/huggingface` | Caché de modelos de Hugging Face |
| `OCR_ENGINE` | `easyocr` | Motor OCR primario (`easyocr`, `rapidocr`, `tesserocr`) |
| `OCR_LANG` | `es,en` | Idiomas del OCR |
| `MAX_FILE_MB` | `50` | Tamaño máximo de archivo aceptado |
| `MAX_PAGES` | `50` | Máximo de páginas por documento |
| `DOCUMENT_TIMEOUT` | `120` | Presupuesto de tiempo por documento (segundos) |


