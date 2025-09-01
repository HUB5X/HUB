# functions/_worker.py
from mangum import Mangum
from main import app

# This handler connects FastAPI to Cloudflare's environment
handler = Mangum(app)
