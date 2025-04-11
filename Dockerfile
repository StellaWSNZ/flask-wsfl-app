FROM python:3.11-slim

# Install ODBC Driver 18
RUN apt-get update && \
    apt-get install -y curl gnupg2 unixodbc-dev && \
    curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - && \
    curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 && \
    pip install --upgrade pip

# Set working directory
WORKDIR /app

# Copy app files
COPY . .

# Install Python deps
RUN pip install -r requirements.txt

# Set Flask port for Render
ENV PORT=10000
EXPOSE 10000

# Run the Flask app
CMD ["python", "app.py"]
