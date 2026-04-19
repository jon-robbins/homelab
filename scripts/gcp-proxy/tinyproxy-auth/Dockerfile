FROM alpine:3.19
RUN apk add --no-cache tinyproxy python3
COPY entrypoint.py /entrypoint.py
RUN chmod +x /entrypoint.py
EXPOSE 8888
ENTRYPOINT ["/entrypoint.py"]
