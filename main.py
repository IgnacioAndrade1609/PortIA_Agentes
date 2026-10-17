import os
from contextlib import asynccontextmanager

import aio_pika
import redis
from fastapi import FastAPI

import agente_inspector.consumer as agente_inspector
import agente_lector.consumer as agente_lector
from shared.logging_config import setup_logging
from shared.queues import declarar_colas

setup_logging()

r = redis.Redis(host="localhost", port=6379, decode_responses=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = await aio_pika.connect_robust(
        os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    )
    channel = await connection.channel()
    colas = await declarar_colas(channel)

    await agente_lector.start(colas["shipment_created"])
    await agente_inspector.start(colas["inspect_container"])

    yield
    await connection.close()


app = FastAPI(lifespan=lifespan)


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
