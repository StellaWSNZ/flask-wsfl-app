FROM python:3.11-slim-bullseye

# Set environment variable to prevent interactive prompts during package installs
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies
RUN apt-get update && \
    apt-get upgrade -y && \  # 💡 Fix broken dependency issue
    apt-get install -y curl gnupg gnupg2 apt-transport-https unixodbc-dev && \
    mkdir -p /etc/apt/keyrings && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/11/prod bullseye main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --upgrade pip

# App setup
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt

# Run app
CMD ["python", "app.py"]
