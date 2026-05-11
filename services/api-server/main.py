from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import valuemap, projects

app = FastAPI(
    title="REALYTICS x PLATFORM9 API",
    description="Backend API server for Real Estate OS alpha workflows",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(valuemap.router)
app.include_router(projects.router)


@app.get("/")
def read_root():
    return {"message": "REALYTICS x PLATFORM9 API Server"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
