FROM python:3.11

WORKDIR /pysts-restapi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["gunicorn", "-c", "gunicorn_config.py", "main:app"]
