FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /version.txt에 표시할 커밋 SHA. CI에서 --build-arg GIT_SHA=<sha>로 주입 (ADR-002)
ARG GIT_SHA=dev
ENV APP_VERSION=${GIT_SHA}

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["./entrypoint.sh"]
