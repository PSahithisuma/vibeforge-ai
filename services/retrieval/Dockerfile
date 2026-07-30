FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# bge-m3 model downloads to /root/.cache — shared via model_cache volume
CMD ["python", "main.py"]
