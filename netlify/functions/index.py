# netlify/functions/index.py
import os, sys
from mangum import Mangum

# adiciona o repo root ao sys.path para importar /api
HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.index import app  # usa seu app FastAPI do /api/index.py

# Netlify procura por "handler"
handler = Mangum(app)