FROM python:3.11-slim-bullseye

# System dependencies and Microsoft ODBC Driver
RUN apt-get update && \
    apt-get install -y \
    curl \
    gnupg \
    gnupg2 \
    apt-transport-https \
    locales \
    unixodbc-dev \
    libgssapi-krb5-2 \
    libunwind8 \
    libssl1.1 && \
    echo "en_US.UTF-8 UTF-8" > /etc/locale.gen && \
    locale-gen && \
    mkdir -p /etc/apt/keyrings && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/11/prod bullseye main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    rm -rf /var/lib/apt/lists/*

# Set UTF-8 locale
ENV LANG en_US.UTF-8

# App setup
WORKDIR /app
COPY . /app
RUN pip install --upgrade pip && pip install -r requirements.txt
RUN pip install pyodbc  

CMD ["python", "run.py"]
