# REALYTICS x PLATFORM9 API Server

FastAPI-based backend for the AI Real Estate OS.
This provides calculations, risk assessment, and report generation for real estate PF.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run tests:
   ```bash
   pytest tests/ -v
   ```

3. Run server:
   ```bash
   uvicorn main:app --reload
   ```

## Docker

You can use Docker Compose to run the API:

```bash
docker-compose up -d
```
