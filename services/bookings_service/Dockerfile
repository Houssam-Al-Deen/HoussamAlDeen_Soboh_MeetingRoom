FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy entire project (simple for student setup)
COPY . /app
ENV PYTHONPATH=/app

EXPOSE 8003
CMD ["python", "services/bookings_service/app.py"]
