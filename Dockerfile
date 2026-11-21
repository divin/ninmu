# Ninmu Docker image
# uv-based Python 3.12 image, no heavy dependencies

FROM python:3.12-slim

# Set non-root user
RUN useradd --create-home --shell /bin/bash appuser

# curl is needed by the container health check
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy source (README.md is needed by the build backend for package metadata)
COPY README.md ./
COPY src ./src

# Install the package
RUN uv sync --frozen --no-dev

# Set environment variables
ENV PYTHONUNBUFFERED=1
# Bind to all interfaces on the exposed port inside the container
ENV HOST=0.0.0.0
ENV PORT=3000

# Switch to non-root user
USER appuser

# Expose the FastMCP server port (can be remapped by deployer)
EXPOSE 3000

# Run the installed console script directly — do not let uv re-sync the
# root-owned venv at startup (appuser cannot write to it)
ENV PATH="/app/.venv/bin:${PATH}"
CMD ["ninmu"]
