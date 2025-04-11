#!/bin/bash

# Install ODBC driver
sudo apt-get update
sudo apt-get install -y gnupg curl unixodbc-dev
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/debian/12/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
ACCEPT_EULA=Y sudo apt-get install -y msodbcsql18

# Show available drivers
echo "🔍 ODBC Drivers:"
odbcinst -q -d

# Show path to installed driver
echo "🔍 Driver .so files:"
find / -name "libmsodbcsql-*.so" 2>/dev/null

# Start your app
python3 app.py --host=0.0.0.0 --port=10000

