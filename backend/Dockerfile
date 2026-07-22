FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching. Pure-Python stack → slim is enough.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + committed seed files (runtime data / secrets excluded via .dockerignore).
COPY . .

# Hosts (Render/Koyeb/Fly) inject $PORT; default 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
