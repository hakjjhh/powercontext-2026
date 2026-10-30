FROM python:3.12-slim
WORKDIR /app
COPY bridge/server.py /app/server.py
ENV BRIDGE_PORT=8787 BRIDGE_DB_PATH=/data/scopes.sqlite3
VOLUME ["/data"]
EXPOSE 8787
CMD ["python", "/app/server.py"]