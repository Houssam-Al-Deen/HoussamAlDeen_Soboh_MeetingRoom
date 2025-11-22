FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project (filtered by .dockerignore) so shared package is present
COPY . .

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["python", "-m", "services.users_service.app"]
FROM python:3.12-slim

WORKDIR /app

# Prevent .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy root requirements (build context will be project root via docker-compose)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code and shared module
COPY services/users_service ./services/users_service
COPY shared ./shared

ENV PORT=8001
EXPOSE 8001

CMD ["python", "services/users_service/app.py"]
