FROM python:3.12-slim
WORKDIR /demo
COPY server.py mode.txt ./
USER 65534:65534
EXPOSE 8080
CMD ["python","server.py"]
