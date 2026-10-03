
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    dos2unix \
    cifs-utils \
    smbclient \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
# netcat-traditional bind9-host iputils-ping iproute2 net-tools

WORKDIR /app

COPY requirements.txt .

RUN cat requirements.txt | dos2unix > requirements.txt.new
RUN echo "psycopg2-binary>=2.9.9" >> requirements.txt.new
#RUN pip install --no-cache-dir -r requirements.txt.new
RUN pip install -r requirements.txt.new


COPY . /app


#CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
#CMD ["/usr/local/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "log_config.yaml"]
ENTRYPOINT ["./entrypoint.sh"]