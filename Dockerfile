FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    wget \
    autoconf \
    automake \
    libtool \
    && rm -rf /var/lib/apt/lists/*

RUN wget https://github.com/TA-Lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz && \
    tar -xzf ta-lib-0.6.4-src.tar.gz && \
    cd ta-lib-0.6.4 && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib-0.6.4 ta-lib-0.6.4-src.tar.gz

WORKDIR /app
RUN mkdir -p log/main log/test
RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src/main/ .

CMD ["python", "-u", "app.py"]