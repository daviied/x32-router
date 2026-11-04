FROM python:3.12-alpine
WORKDIR /app
COPY app/ /app/
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8080
RUN adduser -D -u 1000 app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- "http://127.0.0.1:${PORT}/api/auth" || exit 1
CMD ["python", "server.py"]
