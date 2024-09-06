# Use the official Super-Linter image as the base image
FROM ghcr.io/github/super-linter:latest

# Install flake8-black
RUN pip install flake8-black
