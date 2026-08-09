from pathlib import Path
from dotenv import load_dotenv


load_dotenv()


from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
templates = Jinja2Templates(directory="templates")
import os
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from models.schemas import ChatRequest, ChatResponse, HealthResponse
from services.openai_service import OpenAIService
from services.pinecone_service import init_index
from services.tessa_graph import run_tessa
# Connect static folder (CSS, JS, images)

# Connect templates folder
templates = Jinja2Templates(directory="templates")
# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatbot")

# Initialization
app = FastAPI(title="TESSA")   # <-- this must come BEFORE app.mount()
# Create directories if they don't exist
STATIC_DIR = Path("static")
TEMPLATES_DIR = Path("templates")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "css").mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "js").mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(directory="templates")



# Middleware
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static Files & Templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Service instance (openai_service kept available for a manual provider swap;
# response generation now runs inside services/tessa_graph.py)
openai_service = OpenAIService()


@app.on_event("startup")
async def startup_event():
    # Connects to the TESSA knowledge index, creating it only if it doesn't
    # exist yet. Safe to run on every startup - never recreates an existing
    # index. If Pinecone isn't configured, this logs a warning and /chat
    # will simply fall back to the escalation flow instead of crashing.
    init_index()


@app.get("/", response_class=HTMLResponse)
async def serve_frontend(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )

@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}

@app.post("/chat")
async def chat(request: Request):

    data = await request.json()

    message = data.get("message")
    history = data.get("history") or []  # main.js already sends this; the server just wasn't using it before

    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty.")

    # Runs the full TESSA workflow: language detection -> intent detection
    # (with follow-up-aware query rewriting from history) -> RAG retrieval ->
    # response generation -> safety check -> channel formatting.
    result = run_tessa(message, history=history, channel="web")

    logger.info(
        "Chat response generated | intent=%s | knowledge_items=%d | escalate=%s",
        result.get("intent"),
        len(result.get("knowledge_hits") or []),
        result.get("escalate"),
    )

    return {
        "response": result.get("response", ""),
        "intent": result.get("intent"),
        "escalate": result.get("escalate", False),
    }
    
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty.")

    try:
        def stream_generator():
            response = openai_service.client.chat.completions.create(
                model=openai_service.model,
                messages=[
                    {"role": "system", "content": openai_service.system_prompt},
                    *([{"role": m.role, "content": m.content} for m in req.history] if req.history else []),
                    {"role": "user", "content": req.message}
                ],
                stream=True
            )
            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        return StreamingResponse(stream_generator(), media_type="text/plain")

    except Exception as e:
        logger.exception("Chat completion failed")
        raise HTTPException(
            status_code=502,
            detail="The chatbot service is temporarily unavailable."
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)