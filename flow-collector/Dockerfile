FROM netsampler/goflow2:v2.2.1 AS goflow

FROM python:3.12-alpine
COPY --from=goflow /goflow2 /usr/local/bin/goflow2
RUN pip install --no-cache-dir httpx==0.28.1 maxminddb==2.6.2 \
 && adduser -D -H -u 10001 -s /sbin/nologin harbor
WORKDIR /srv
COPY collector.py .
ENV PYTHONUNBUFFERED=1
EXPOSE 2055/udp 6343/udp
# Not root: the same user as the backend, which writes collector.json for it.
USER 10001:10001
CMD ["python", "collector.py"]
