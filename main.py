from fastapi import FastAPI
import redis

app = FastAPI()
r = redis.Redis(host="localhost", port=6379, decode_responses=True)

@app.get("/")
def read_root():
    return {"mensaje": "Agente Lector de PortIA activo"}

@app.get("/guardar-prueba")
def guardar_prueba():
    r.set("ultimo_container", "IKSU5203585")
    return {"guardado": True}

@app.get("/leer-prueba")
def leer_prueba():
    return {"ultimo_container": r.get("ultimo_container")}