FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install "uvicorn[standard]" websockets

RUN pip install -r requirements.txt


COPY . /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
