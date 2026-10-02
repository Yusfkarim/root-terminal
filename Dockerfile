FROM python:3.12-slim
WORKDIR /app
COPY app.py start.sh ./
RUN chmod +x start.sh
ENV PORT=8080
EXPOSE 8080
CMD ["./start.sh"]
