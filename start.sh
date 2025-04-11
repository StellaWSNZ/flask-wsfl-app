#!/bin/bash

# Update and install ODBC Driver 18 with sudo
sudo apt-get update
sudo apt-get install -y gnupg curl unixodbc-dev
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/debian/12/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
ACCEPT_EULA=Y sudo apt-get install -y msodbcsql18

# Run your app on 0.0.0.0 for external access
python3 app.py --host=0.0.0.0 --port=10000
