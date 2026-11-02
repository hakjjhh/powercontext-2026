# Offline variant of powercontext.Dockerfile: identical image, but the wheels
# come from a local wheelhouse instead of PyPI.
#
#   pip download -d deploy/wheelhouse "powercontext[cli,server,seekdb]==1.2.0"
#   POWERCONTEXT_DOCKERFILE=deploy/powercontext-offline.Dockerfile bash dify/scripts/start_all.sh
#
# Use it when PyPI is reachable but slow: the 54 MB pylibseekdb wheel is then
# downloaded once and shared by the container build and a native install.
FROM python:3.12-slim

COPY deploy/wheelhouse /wheelhouse
# See powercontext.Dockerfile: pymysql must stay on 1.1.x for aiomysql 0.3.2.
RUN pip install --no-cache-dir --no-index --find-links /wheelhouse \
      "powercontext[cli,server,seekdb]==1.2.0" "pymysql>=1.1,<1.2"

ENV POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0 \
    POWERCONTEXT_SERVER_DATABASE_KIND=seekdb \
    POWERCONTEXT_SERVER_DATABASE_PATH=/data/seekdb \
    POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["powercontext"]
CMD ["server", "run"]
