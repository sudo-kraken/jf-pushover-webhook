# Use an official Python runtime as a parent image
FROM --platform=$TARGETPLATFORM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Copy the application files
COPY app /app

# Install dependencies
RUN pip install --no-cache-dir Flask==2.3.2 requests==2.31.0 gunicorn==21.2.0

# Expose the port the app runs on
EXPOSE 6969

# Add a health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD curl -f http://localhost:6969/ || exit 1

# Define the command to run the application using Gunicorn
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]