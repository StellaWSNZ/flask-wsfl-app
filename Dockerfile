FROM python:3.11-slim-bullseye

# Install dependencies & add Microsoft package signing key
RUN apt-get update && \
    apt-get install -y curl gnupg2 unixodbc-dev apt-transport-https && \
    mkdir -p /etc/apt/keyrings && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/keyrings/microsoft.gpg && \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/11/prod bullseye main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    pip install --upgrade pip

# Set working directory
WORKDIR /app

# Copy app
COPY . .

# Install Python dependencies
RUN pip install -r requirements.txt

# Expose Flask port
ENV PORT=10000
EXPOSE 10000

# Start app
CMD ["python", "app.py"]
