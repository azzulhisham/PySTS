# activate virtual environment
source venv/bin/activate


# -----  Streamlit Application  -----
# run the streamlit application using the following command
streamlit run ./st_app/app.py --server.port 8080


# -----  Web Application (backend)  -----
python3 socket_server.py 


# -----  Web Application (frontend)  -----
python3 app.py


# Python Library
pip install langchain-google-genai





