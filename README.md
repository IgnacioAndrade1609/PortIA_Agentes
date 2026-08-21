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
  
  
