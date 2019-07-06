#!/usr/bin/python3
from flask import Flask, render_template, request, redirect
import colorhelper as ch
import requests
import json
import threading
import socket
import re
import os
import mysql.connector

ip=os.environ['BRIDGE_IP']
apikey=os.environ['BRIDGE_USER']
mysql_ip=os.environ['MYSQL_HOST']
mysql_pw=os.environ['MYSQL_PW']
mysql_user=os.environ['MYSQL_USER']
base_url = f'http://{ip}/api/{apikey}'

app = Flask(__name__)
color_helper = ch.ColorHelper(gamut=ch.GamutC)
bridges = []

scenes_table_sql = (
    "CREATE TABLE IF NOT EXISTS `scenes` ("
    "   `id` int NOT NULL AUTO_INCREMENT,"
    "   `name` nvarchar(255) NOT NULL,"
    "   `room_id` int NOT NULL,"
    "   PRIMARY KEY (`id`)"
    ") ENGINE=InnoDB"
)

scene_lights_table_sql = (
    "CREATE TABLE IF NOT EXISTS `scene_lights` ("
    "   `id` int NOT NULL AUTO_INCREMENT,"
    "   `scene_id` int NOT NULL,"
    "   `colour_id` int NOT NULL,"
    "   PRIMARY KEY (`id`)"
    ") ENGINE=InnoDB"
)

scene_colours_table_sql = (
    "CREATE TABLE IF NOT EXISTS `scene_colours` ("
    "   `id` int NOT NULL AUTO_INCREMENT,"
    "   `scene_id` int NOT NULL,"
    "   `x` float NOT NULL,"
    "   `y` float NOT NULL,"
    "   PRIMARY KEY (`id`)"
    ") ENGINE=InnoDB"
)

get_scenes_sql = ("SELECT * FROM scenes WHERE room_id = %s")
get_colours_sql = ("SELECT * FROM scene_colours WHERE scene_id = %s")
scene_timers = {}

class SceneState:
    def __init__(self, colours, init_map):
        self.colours = colours
        self.light_map = init_map

    def change_lights(self):
        url = f'{base_url}/lights'
        for light_id, index in self.light_map.items():
            colour = self.colours[index]
            data = {
                'xy': [colour[0], colour[1]],
                'transitiontime': 50
            }
            print(data)
            requests.put(f'{url}/{light_id}/state', data=json.dumps(data))

    def next_state(self):
        print(self.light_map)
        num = len(self.colours)
        for light, colour_state in self.light_map.items():
            self.light_map[light] = (colour_state + 1) % num
        print(self.light_map)

@app.route("/")
def index():
    return render_template('home.html', rooms=get_group_ids())

@app.route('/rooms/<room_id>')
def room(room_id):
    scenes=[]
    (cnx, cur) = open_sql()
    cur.execute(get_scenes_sql, (room_id,))
    for scene_id, name, room_id in cur:
        scenes.append({'id': scene_id, 'name': name})
    return render_template('rooms.html', scenes=scenes) 

@app.route('/rooms/<id>/createscene', methods=['GET', 'POST'])
def create_scene(id):
    if request.method == 'GET':
        return render_template('create_scene.html', lights=get_lights(id))
    elif request.method == 'POST':
        return create_scene_post(id)

@app.route('/rooms/<room_id>/scenes/<scene_id>', methods=['GET', 'POST'])
def scenes(room_id, scene_id):
    if request.method == 'GET':
        return scenes_get(room_id, scene_id)
    elif request.method == 'POST':
        print(request.form)
        colours = get_xy_colours(scene_id)
        init_state = {}
        for k, v in request.form.items():
            if 'light-' in k:
                light_id = k.split('-')[1]
                index = int(v) - 1
                init_state[light_id] = index
        state = SceneState(colours, init_state)
        state.change_lights()
        if request.form['loop'] == 'true':
            print('creating timer')
            if room_id in scene_timers:
                scene_timers[room_id].cancel()
            time = int(request.form['transitionTime'])
            t = threading.Timer(time, loop_timer, [state, time, room_id])
            t.start()
            scene_timers[room_id] = t

        return scenes_get(room_id, scene_id)
    
@app.route('/rooms/<room_id>/scenes/<scene_id>/delete')
def delete_room(room_id, scene_id):
    cnx, cur = open_sql()
    cur.execute("DELETE FROM scenes WHERE scene_id = %s", (scene_id, ))
    cur.execute("DELETE FROM scene_lights WHERE scene_id = %s", (scene_id, ))
    cur.execute("DELETE FROM scene_colours WHERE scene_id = %s", (scene_id, ))
    cnx.commit()
    cur.close()
    cnx.close()
    return redirect(f'/rooms/{room_id}')

def loop_timer(state, time, room_id):
    print('changing states')
    state.next_state()
    state.change_lights()
    t = threading.Timer(time, loop_timer, [state, time, room_id])
    t.start()
    scene_timers[room_id] = t

def scenes_get(room_id, scene_id):
    lights = get_lights(room_id)
    colours = get_colours(scene_id)
    (cnx, cur) = open_sql()
    cur.execute("SELECT name FROM scenes WHERE id = %s", (scene_id,))
    data = {
        'name': cur.fetchone()[0],
        'colors': colours,
        'lights': lights
    }
    cur.close()
    cnx.close()
    return render_template("scene.html", scene=data, back=f'/rooms/{room_id}')

def to_dict(tup):
    dict((x, y) for x, y in tup)

def get_colours(scene_id):
    colours = []
    (cnx, cur) = open_sql()
    cur.execute(get_colours_sql, (scene_id,))
    for id, _, x, y in cur:
        (r, g, b) = color_helper.get_rgb_from_xy_and_brightness(x, y)
        colour = {
            'id': id,
            'hex': color_helper.rgb_to_hex(r, g, b)
        }
        colours.append(colour)
    cur.close()
    cnx.close()
    return colours

def get_xy_colours(scene_id):
    colours = []
    (cnx, cur) = open_sql()
    cur.execute(get_colours_sql, (scene_id,))
    for id, _, x, y in cur:
        colours.append((x,y))
    cur.close()
    cnx.close()
    return colours

def create_scene_post(room_id):
    scene_name = request.form['name']
    colours=[]
    for n, v in request.form.items():
        if 'hexColour-' in n:
            if v[0] == '#':
                colours.append(v[1:])
            else:
                colours.append(v)
    
    (cnx, cursor) = open_sql()
    room_sql = 'INSERT INTO `scenes` (name, room_id) VALUES (%s, %s)'
    scene_colours_sql = 'INSERT INTO `scene_colours` (scene_id, x, y) VALUES (%s, %s, %s)'
    cursor.execute(room_sql, (scene_name, room_id))
    scene_id = cursor.lastrowid
    for colour in colours:
        (r, g, b) = color_helper.hex_to_rgb(colour)
        (x, y) = color_helper.get_xy_point_from_rgb(r, g, b)
        cursor.execute(scene_colours_sql, (scene_id, x, y))
    cnx.commit()
    cursor.close()
    cnx.close()
    return redirect(f'/rooms/{room_id}/scenes/{scene_id}')

def get_group_ids():
    r = requests.get(f'{base_url}/groups')
    room_data=[]
    for room_id, room_detail in r.json().items():
        room_data.append({'id': room_id, 'name': room_detail['name']})
    return room_data

def get_lights(room):
    r = requests.get(f'{base_url}/groups')
    light_ids = r.json()[room]['lights']
    light_data=[]
    r = requests.get(f'{base_url}/lights')
    light_json=r.json()
    for light_id in light_ids:
        light=light_json[light_id]
        light={
            'id': light_id,
            'name': light['name']
        }
        light_data.append(light)
    return light_data

def open_sql():
    connection = mysql.connector.connect(user=mysql_user, password=mysql_pw,
                                    host=mysql_ip, database='hue')
    cursor = connection.cursor()
    return (connection, cursor)

class HueBridge:
    def __init__(self, name, ip):
        self.name = name
        self.ip = ip

def init():
    (connection, cursor) = open_sql()
    cursor.execute(scenes_table_sql)
    cursor.execute(scene_lights_table_sql)
    cursor.execute(scene_colours_table_sql)
    connection.commit()
    cursor.close()
    connection.close()

if __name__ == "__main__":
    init()
    app.run(host='0.0.0.0', port=80)

