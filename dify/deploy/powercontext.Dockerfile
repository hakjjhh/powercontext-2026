# PowerContext Server with the embedded seekdb storage engine.
# The bridge talks to it over HTTP; seekdb lives inside this container under
# /data/seekdb (POWERCONTEXT_SERVER_DATABASE_KIND=seekdb).
FROM python:3.12-slim

# pymysql is pinned to 1.1.x on purpose: aiomysql 0.3.2 imports
# `escape_bytes_prefixed` from pymysql.converters, and pymysql >=1.2 no longer
# exports it, so every write through the embedded seekdb engine dies with
# `TypeError: 'str' object is not callable`.
RUN pip install --no-cache-dir --timeout 120 --retries 8 \
      "powercontext[cli,server,seekdb]==1.2.0" "pymysql>=1.1,<1.2"

ENV POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0 \
    POWERCONTEXT_SERVER_DATABASE_KIND=seekdb \
    POWERCONTEXT_SERVER_DATABASE_PATH=/data/seekdb \
    POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["powercontext"]
CMD ["server", "run"]
