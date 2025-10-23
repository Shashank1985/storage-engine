# Use a lightweight official Python image
FROM python:3.11-slim

# Set environment variables for the server defaults
# The default port is 8000, as defined in src/bloomkv/server.py
ENV BLOOMKV_SERVER_PORT=8000

# Set the working directory inside the container
WORKDIR /app

# Copy the minimum files needed for installation and running
# The structure assumes the source code is within 'src/bloomkv' and the metadata is in 'pyproject.toml'
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

# Install the project from pyproject.toml using the modern PEP 517 approach.
# The 'requests' package is only needed for the CLI, but is a project dependency.
# The dependencies are listed in pyproject.toml (e.g., sortedcontainers, fastapi, uvicorn, msgpack)
RUN pip install --no-cache-dir .

# Expose the default port (8000) for network communication
EXPOSE 8000

# Volume for persistent storage: 
# The server defaults to storing data in 'bloomkv_server_data' in the current working directory (/app)
VOLUME /app/bloomkv_server_data

# The default command to run the BloomKV server
# This executes the script defined in pyproject.toml as 'bloomkv-server'
CMD ["bloomkv-server"]