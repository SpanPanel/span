# Use the official Super-Linter image as the base image
FROM ghcr.io/github/super-linter:v4.9.7

# Create a non-root user and switch to that user
RUN addgroup --system lintgroup && adduser --system --ingroup lintgroup lintuser
USER lintuser

# Install flake8-black
RUN pip install flake8-black

# Add HEALTHCHECK instructions
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1
