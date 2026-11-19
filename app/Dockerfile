FROM python:3.13-slim
COPY server/ /app/server/
COPY web/ /app/web/
CMD ["python", "/app/server/server.py", "--host", "0.0.0.0", "--port", "8000", "--data", "/data", "--static", "/app/web"]
