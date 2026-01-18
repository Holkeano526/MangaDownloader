import os
from scrapegraphai.graphs import SmartScraperGraph

# Configuración (Usa tu clave de Google)
graph_config = {
    "llm": {
        "api_key": "AIzaSyBXzU2iIbOTWjiPsGyuXeT3aRwDampVps0",
        "model": "gemini/gemini-1.5-flash",
    },
    "verbose": True,
    "headless": False, # Pon True para que no se vea el navegador
}

# La magia
smart_scraper_graph = SmartScraperGraph(
    prompt="Extrae todos los precios y nombres de productos de esta página y dámelos en JSON",
    source="https://www.prestamype.com/", # La URL que quieras
    config=graph_config
)

result = smart_scraper_graph.run()
print(result)