FROM python:3.11-slim-bullseye

# System dependencies
RUN apt-get update && \
    apt-get install -y curl gnupg gnupg2 apt-transport-https unixodbc-dev && \
    mkdir -p /etc/apt/keyrings && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/11/prod bullseye main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /app
COPY . /app
RUN pip install --upgrade pip && pip install -r requirements.txt

CMD ["python", "app.py"]
