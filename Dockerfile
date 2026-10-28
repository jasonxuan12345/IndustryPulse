FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . .
RUN python -m pip install --no-cache-dir -e . \
    && useradd --create-home --uid 10001 industrypulse \
    && mkdir -p data artifacts \
    && chown -R industrypulse:industrypulse data artifacts
USER industrypulse
EXPOSE 8765
CMD ["python", "scripts/start_dashboard.py", "--host", "0.0.0.0", "--port", "8765"]
