"""Iniciador local sencillo para probar el panel sin Docker."""

import threading
import webbrowser

import uvicorn


URL = "http://127.0.0.1:8088"


def open_panel() -> None:
    webbrowser.open(URL)


if __name__ == "__main__":
    print("DJGABO Engine se está iniciando...")
    print(f"El panel se abrirá en {URL}")
    print("Mantén esta ventana abierta mientras pruebas el panel.")
    print("Para detenerlo, presiona Ctrl+C o cierra esta ventana.")
    threading.Timer(1.5, open_panel).start()
    uvicorn.run("app.api.main:app", host="127.0.0.1", port=8088)

