FROM tiangolo/uwsgi-nginx-flask:python3.7
COPY . /app
RUN pip install -r requirements.txt
ENV FLASK_APP scenes.py