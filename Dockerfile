FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm bash git curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app.py start.sh ui.html icon-192.png icon-512.png ./
RUN chmod +x start.sh
ENV PORT=8080
EXPOSE 8080
CMD ["./start.sh"]
