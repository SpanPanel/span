# Use the official Super-Linter image as the base image
FROM ghcr.io/github/super-linter:v4.9.7

# Create a non-root user and switch to that user
RUN addgroup --system lintgroup && adduser --system --ingroup lintgroup lintuser
USER lintuser

# Install flake8-black
RUN pip install --no-cache-dir flake8-black==0.3.6

# Add HEALTHCHECK instructions
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost/ || exit 1


# To run the linter locally:
# docker build --platform linux/amd64 -t my-super-linter .

# docker run --platform linux/amd64 \
#   -e RUN_LOCAL=true \
#   -e DEFAULT_BRANCH=main \
#   -e SHELL=/bin/bash \
#   -e LINTER_RULES_PATH=".github/linters" \
#   -e LOG_FILE=".github/linters/super-linter.log" \
#   -v $(pwd):/tmp/lint \
#   my-super-linter 