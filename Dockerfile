FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt requirements-cloud.txt ./
RUN python -m pip install --no-cache-dir -r requirements-cloud.txt
COPY corte_sk2 ./corte_sk2
RUN useradd --uid 10001 --create-home corte
USER corte
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 1 --timeout 120 'corte_sk2.cloud:create_app()'"]
