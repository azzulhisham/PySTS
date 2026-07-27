# activate virtual environment
source venv/bin/activate


# -----  Streamlit Application  -----
# run the streamlit application using the following command
streamlit run ./st_app/app.py --server.port 8080


# -----  Web Application (backend)  -----
python3 socket_server.py 


# -----  Web Application (frontend)  -----
python3 app.py


# -----  MANTIS API (Anchorage Polygons REST)  -----
# see restapi/README.md for full details
cd restapi
pip install -r requirements.txt
gunicorn -c gunicorn_config.py main:app
# Swagger UI: http://localhost:8080/swagger


# Python Library
pip install langchain-google-genai





