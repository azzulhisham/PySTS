import requests
import json
import os
import datetime

from flask import Flask, render_template, url_for, request, redirect
from flask_restful import Api, Resource, reqparse, abort, request
from flask_cors import cross_origin
from flask_cors import CORS




app = Flask(__name__)
api = Api(app)
cors = CORS(app, resources={r"/*": {"origins": "*"}})

@app.route("/")
@app.route("/home")
@app.route("/index")
def home(): 
    return render_template('login.html', title="PiNC NAVIGATION")    


@app.route("/playback")
def playback():
    return render_template('playback.html', title="PiNC NAVIGATION")



   

if __name__ == '__main__':
    # app.run(debug=True)
    app.run(host="0.0.0.0", port=os.environ['py_flask_port'] if os.environ.get('py_flask_port') else 3838)