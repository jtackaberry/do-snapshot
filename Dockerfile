FROM python:3.11-alpine
WORKDIR /
RUN pip3 install requests
COPY do-snapshot.py /
ENTRYPOINT ["python3", "/do-snapshot.py"]
