import json
import re
import traceback
from threading import Thread

from sqlalchemy import exc, insert, update
from bs4 import BeautifulSoup
import pika

from opentakserver.extensions import socketio
from opentakserver.functions import datetime_from_iso8601_string
from opentakserver.models.Chatrooms import Chatroom
from opentakserver.models.Alert import Alert
from opentakserver.models.CasEvac import CasEvac
from opentakserver.models.ChatroomsUids import ChatroomsUids
from opentakserver.models.CoT import CoT
from opentakserver.models.EUD import EUD
from opentakserver.models.GeoChat import GeoChat
from opentakserver.models.Icon import Icon
from opentakserver.models.RBLine import RBLine
from opentakserver.models.Video import Video
from opentakserver.models.ZMIST import ZMIST
from opentakserver.models.Point import Point
from opentakserver.models.Marker import Marker


class CoTController:
    def __init__(self, context, logger, db, socketio):
        self.context = context
        self.logger = logger
        self.db = db
        self.socketio = socketio

        self.online_euds = {}
        self.online_callsigns = {}
        self.exchanges = []

        # RabbitMQ
        try:
            self.rabbit_connection = pika.SelectConnection(pika.ConnectionParameters('localhost'),
                                                           self.on_connection_open)
            self.rabbit_channel = None
            self.iothread = Thread(target=self.rabbit_connection.ioloop.start)
            self.iothread.daemon = True
            self.iothread.start()
            self.is_consuming = False
        except BaseException as e:
            self.logger.error("cot_controller - Failed to connect to rabbitmq: {}".format(e))
            return

    def on_connection_open(self, connection):
        self.rabbit_connection.channel(on_open_callback=self.on_channel_open)
        self.rabbit_connection.add_on_close_callback(self.on_close)

    def on_channel_open(self, channel):
        self.rabbit_channel = channel
        self.rabbit_channel.queue_declare(queue='cot_controller')
        self.rabbit_channel.exchange_declare(exchange='cot_controller', exchange_type='fanout')
        self.rabbit_channel.queue_bind(exchange='cot_controller', queue='cot_controller')
        self.rabbit_channel.basic_consume(queue='cot_controller', on_message_callback=self.on_message, auto_ack=True)
        self.rabbit_channel.add_on_close_callback(self.on_close)

    def on_close(self, channel, error):
        self.logger.error("cot_controller closing RabbitMQ connection: {}".format(error))

    def parse_device_info(self, uid, soup, event):
        link = event.find('link')
        fileshare = event.find('fileshare')

        # Don't parse server generated messages
        with self.context:
            if uid == self.context.app.config.get("OTS_NODE_ID"):
                return

        callsign = None
        phone_number = None

        if uid not in self.online_euds and not uid.endswith('ping'):

            contact = event.find('contact')
            if contact:
                if 'callsign' in contact.attrs:
                    callsign = contact.attrs['callsign']

                    if callsign not in self.online_callsigns:
                        self.online_callsigns[callsign] = {'uid': uid, 'cot': soup}

                    # Declare a RabbitMQ Queue for this uid and join the 'dms' and 'cot' exchanges
                    if self.rabbit_channel and self.rabbit_channel.is_open:
                        self.rabbit_channel.queue_bind(exchange='dms', queue=uid, routing_key=uid)
                        self.rabbit_channel.queue_bind(exchange='chatrooms', queue=uid,
                                                       routing_key='All Chat Rooms')

                        for eud in self.online_euds:
                            self.rabbit_channel.basic_publish(exchange='dms',
                                                              routing_key=uid,
                                                              body=json.dumps({'cot': str(self.online_euds[eud]['cot']),
                                                                               'uid': None}))

                        self.online_euds[uid] = {'cot': str(soup), 'callsign': callsign}

                if 'phone' in contact.attrs:
                    phone_number = contact.attrs['phone']

            takv = event.find('takv')
            if takv:
                device = takv.attrs['device']
                os = takv.attrs['os']
                platform = takv.attrs['platform']
                version = takv.attrs['version']

                eud = EUD()
                eud.uid = uid
                eud.callsign = callsign
                eud.device = device
                eud.os = os
                eud.platform = platform
                eud.version = version
                eud.phone_number = phone_number
                eud.last_event_time = datetime_from_iso8601_string(event.attrs['start'])
                eud.last_status = 'Connected'

                with self.context:
                    try:
                        self.db.session.add(eud)
                        self.db.session.commit()
                    except exc.IntegrityError as e:
                        # This EUD/uid is already in the DB, update it in case anything changed like callsign or app version
                        self.db.session.rollback()
                        eud = self.db.session.execute(self.db.select(EUD).filter_by(uid=uid)).scalar_one()
                        eud.callsign = callsign
                        eud.os = os
                        eud.device = device
                        eud.platform = platform
                        eud.version = version
                        eud.phone_number = phone_number
                        eud.last_event_time = datetime_from_iso8601_string(event.attrs['start'])
                        eud.last_status = 'Connected'
                        self.db.session.commit()
                        self.logger.debug("Updated {}".format(uid))
        elif event.find('takv'):
            self.online_euds[uid]['cot'] = str(soup)

    def insert_cot(self, soup, event, uid):
        try:
            sender_callsign = self.online_euds[uid]['callsign']
        except:
            sender_callsign = 'server'

        start = datetime_from_iso8601_string(event.attrs['start'])
        stale = datetime_from_iso8601_string(event.attrs['stale'])
        timestamp = datetime_from_iso8601_string(event.attrs['time'])

        with self.context:
            res = self.db.session.execute(insert(CoT).values(
                how=event.attrs['how'], type=event.attrs['type'], sender_callsign=sender_callsign,
                sender_uid=uid, timestamp=timestamp, xml=str(soup), start=start, stale=stale
            ))

            self.db.session.commit()
            return res.inserted_primary_key[0]

    def parse_point(self, event, uid, cot_id):
        # hae = Height above the WGS ellipsoid in meters
        # ce = Circular 1-sigma or a circular area about the location in meters
        # le = Linear 1-sigma error or an altitude range about the location in meters
        point = event.find('point')
        if point:
            p = Point()
            p.uid = event.attrs['uid']
            p.device_uid = uid
            p.ce = point.attrs['ce']
            p.hae = point.attrs['hae']
            p.le = point.attrs['le']
            p.latitude = float(point.attrs['lat'])
            p.longitude = float(point.attrs['lon'])
            p.timestamp = datetime_from_iso8601_string(event.attrs['time'])
            p.cot_id = cot_id

            # We only really care about the rest of the data if there's a valid lat/lon
            if p.latitude == 0 and p.longitude == 0:
                return None

            track = event.find('track')
            if track:
                p.course = track.attrs['course'] if 'course' in track.attrs else 0
                p.speed = track.attrs['speed'] if 'speed' in track.attrs else 0

            precision_location = event.find('precisionlocation')
            if precision_location and 'geolocationsrc' in precision_location.attrs:
                p.location_source = precision_location.attrs['geolocationsrc']
            elif precision_location and 'altsrc' in precision_location.attrs:
                p.location_source = precision_location.attrs['altsrc']

            status = event.find('status')
            if status:
                if 'battery' in status.attrs:
                    p.battery = status.attrs['battery']

            with self.context:
                res = self.db.session.execute(insert(Point).values(
                    uid=p.uid, device_uid=p.device_uid, ce=p.ce, hae=p.hae, le=p.le, latitude=p.latitude,
                    longitude=p.longitude, timestamp=p.timestamp, cot_id=cot_id, location_source=p.location_source,
                    course=p.course, speed=p.speed, battery=p.battery)
                )

                self.db.session.commit()
                p = self.db.session.execute(
                    self.db.session.query(Point).filter(Point.id == res.inserted_primary_key[0])).first()[0]

                # This CoT is a position update for an EUD, send it to socketio clients, so it can be seen on the UI map
                if event.find('takv'):
                    socketio.emit('point', p.to_json(), namespace='/socket.io')

                return res.inserted_primary_key[0]

    def parse_geochat(self, event, cot_id, point_pk):
        chat = event.find('__chat')
        if chat:
            chat_group = event.find('chatgrp')
            remarks = event.find('remarks')

            chatroom = Chatroom()

            chatroom.name = chat.attrs['chatroom']
            chatroom.id = chat.attrs['id']
            chatroom.parent = chat.attrs['parent']

            with self.context:
                try:
                    self.db.session.add(chatroom)
                    self.db.session.commit()
                except exc.IntegrityError:
                    self.db.session.rollback()

            geochat = GeoChat()

            geochat.uid = event.attrs['uid']
            geochat.chatroom_id = chat.attrs['id']
            geochat.sender_uid = chat_group.attrs['uid0']
            geochat.remarks = remarks.text
            geochat.timestamp = datetime_from_iso8601_string(remarks.attrs['time'])
            geochat.point_id = point_pk
            geochat.cot_id = cot_id

            with self.context:
                try:

                    self.db.session.add(geochat)
                    self.db.session.commit()
                except exc.IntegrityError:
                    # TODO: Check if remarks can be edited and if so do an update here
                    self.db.session.rollback()

            for attr in chat_group.attrs:
                if attr.startswith("uid") and attr != 'uid':

                    if chat.attrs['groupOwner'].lower() == 'true' and attr == 'uid0':
                        with self.context:
                            self.db.session.execute(update(Chatroom).where(Chatroom.id == chat.attrs['id'])
                                                    .values(group_owner=chat_group.attrs[attr]))
                            self.db.session.commit()

                    chatroom_uid = ChatroomsUids()
                    chatroom_uid.chatroom_id = chat.attrs['id']
                    chatroom_uid.uid = chat_group.attrs[attr]

                    with self.context:
                        try:
                            self.db.session.add(chatroom_uid)
                            self.db.session.commit()
                            self.logger.debug(
                                "add {} to chatroom {}".format(chatroom_uid.uid, chatroom_uid.chatroom_id))
                        except exc.IntegrityError:
                            self.db.session.rollback()

    def parse_video(self, event, cot_pk):
        video = event.find("__video")
        if video:
            self.logger.debug("Got video stream")
            connection_entry = video.find('ConnectionEntry')

            path = connection_entry.attrs['path']
            if path.startswith("/"):
                path = path[1:]

            v = Video()
            v.network_timeout = connection_entry.attrs['networkTimeout']
            v.uid = connection_entry.attrs['uid']
            v.path = path
            v.protocol = connection_entry.attrs['protocol']
            v.buffer_time = connection_entry.attrs['bufferTime']
            v.address = connection_entry.attrs['address']
            v.port = connection_entry.attrs['port']
            v.rover_port = connection_entry.attrs['roverPort']
            v.rtsp_reliable = connection_entry.attrs['rtspReliable']
            v.ignore_embedded_klv = (connection_entry.attrs['ignoreEmbeddedKLV'].lower() == 'true')
            v.alias = connection_entry.attrs['alias']
            v.cot_id = cot_pk
            v.generate_xml()

            with self.context:
                try:
                    self.db.session.add(v)
                    self.db.session.commit()
                    self.logger.debug("Added video")
                except exc.IntegrityError as e:
                    self.db.session.rollback()
                    self.db.session.execute(update(Video).where(Video.uid == connection_entry.attrs['uid'])
                                            .values(network_timeout=connection_entry.attrs['networkTimeout'],
                                                    protocol=connection_entry.attrs['protocol'],
                                                    buffer_time=connection_entry.attrs['bufferTime'],
                                                    address=connection_entry.attrs['address'],
                                                    port=connection_entry.attrs['port'],
                                                    rover_port=connection_entry.attrs['roverPort'],
                                                    rtsp_reliable=connection_entry.attrs['rtspReliable'],
                                                    ignore_embedded_klv=(connection_entry.attrs[
                                                                             'ignoreEmbeddedKLV'].lower() == 'true'),
                                                    alias=connection_entry.attrs['alias'],
                                                    xml=v.xml))

                    self.db.session.commit()

    def parse_groups(self, event, uid):
        groups = event.find_all('__group')

        for group in groups:
            # Declare an exchange for each group and bind the callsign's queue
            if self.rabbit_channel.is_open and group.attrs['name'] not in self.exchanges:
                self.logger.debug("Declaring exchange {}".format(group.attrs['name']))
                self.rabbit_channel.exchange_declare(exchange=group.attrs['name'])
                self.rabbit_channel.queue_bind(queue=uid, exchange='chatrooms', routing_key=group.attrs['name'])
                self.exchanges.append(group.attrs['name'])

    def parse_alert(self, event, uid, point_pk, cot_pk):
        emergency = event.find('emergency')
        if emergency:
            if 'type' in emergency.attrs:
                emergency_type = emergency.attrs['type']
                alert = Alert()
                alert.sender_uid = uid
                alert.uid = event.attrs['uid']
                alert.start_time = datetime_from_iso8601_string(event.attrs['start'])
                alert.point_id = point_pk
                alert.alert_type = emergency_type
                alert.cot_id = cot_pk

                with self.context:
                    self.db.session.add(alert)
                    self.db.session.commit()
                    socketio.emit('alert', alert.to_json(), namespace='/socket.io')
            elif 'cancel' in emergency.attrs:
                with self.context:
                    try:
                        alert = self.db.session.execute(
                            Alert.query.filter(Alert.cancel_time == None, Alert.sender_uid == uid).order_by(
                                Alert.start_time.desc())).first()[0]
                        alert.cancel_time = datetime_from_iso8601_string(event.attrs['start'])
                        self.db.session.commit()
                        socketio.emit('alert', alert.to_json(), namespace='/socket.io')
                    except BaseException as e:
                        self.logger.error("Failed to set alert cancel time: {}".format(e))

    def parse_casevac(self, event, uid, point_pk, cot_pk):
        medevac = event.find('_medevac_')
        if medevac:
            zmist = medevac.find('zMist')
            with self.context:
                for a in medevac.attrs:
                    if medevac.attrs[a].lower() == 'true':
                        medevac.attrs[a] = True
                    elif medevac.attrs[a].lower() == 'false':
                        medevac.attrs[a] = False

                try:
                    res = self.db.session.execute(
                        insert(CasEvac).values(timestamp=datetime_from_iso8601_string(event.attrs['start']),
                                               sender_uid=uid, uid=event.attrs['uid'],
                                               point_id=point_pk, cot_id=cot_pk,
                                               **medevac.attrs))
                    casevac_pk = res.inserted_primary_key[0]

                    if zmist:
                        self.db.session.execute(insert(ZMIST).values(casevac_uid=event.attrs['uid'], **zmist.attrs))
                except exc.IntegrityError as e:
                    self.db.session.rollback()
                    self.db.session.execute(update(CasEvac).where(CasEvac.uid == event.attrs['uid'])
                                            .values(**medevac.attrs))

                    if zmist:
                        self.db.session.execute(
                            update(ZMIST).where(CasEvac.uid == event.attrs['uid']).values(**zmist.attrs))
                self.db.session.commit()

    def parse_marker(self, event, uid, point_pk, cot_pk):
        if ((re.match("^a-[f|h|u|p|a|n|s|j|k]-[Z|P|A|G|S|U|F]", event.attrs['type']) or
                # Spot map
                re.match("^b-m-p", event.attrs['type'])) and not
                # Don't worry about EUD location updates
                event.find('takv')):
            try:
                marker = Marker()
                marker.uid = event.attrs['uid']
                marker.affiliation = self.get_affiliation(event.attrs['type'])
                marker.battle_dimension = self.get_battle_dimension(event.attrs['type'])

                marker.mil_std_2525c = "s"
                cot_type_list = event.attrs['type'].split("-")
                cot_type_list.pop(0)  # this should always be letter a
                affiliation = cot_type_list.pop(0)
                battle_dimension = cot_type_list.pop(0)
                marker.mil_std_2525c += affiliation
                marker.mil_std_2525c += battle_dimension
                marker.mil_std_2525c += "-"

                for letter in cot_type_list:
                    if letter.isupper():
                        marker.mil_std_2525c += letter

                while len(marker.mil_std_2525c) < 10:
                    marker.mil_std_2525c += "-"

                detail = event.find('detail')
                icon = None

                if detail:
                    for tag in detail:
                        if 'readiness' in tag.attrs:
                            marker.readiness = tag.attrs['readiness'] == "true"
                        if 'argb' in tag.attrs:
                            marker.argb = tag.attrs['argb']
                            marker.color_hex = marker.color_to_hex()
                        if 'callsign' in tag.attrs:
                            marker.callsign = tag.attrs['callsign']
                        if 'iconsetpath' in tag.attrs:
                            marker.iconset_path = tag.attrs['iconsetpath']
                            if marker.iconset_path.lower().endswith('.png'):
                                with self.context:
                                    filename = marker.iconset_path.split("/")[-1]

                                    try:
                                        icon = self.db.session.execute(self.db.session.query(Icon)
                                                                       .filter(Icon.filename == filename)).first()[0]
                                        marker.icon_id = icon.id
                                    except:
                                        icon = self.db.session.execute(self.db.session.query(Icon)
                                        .filter(
                                            Icon.filename == 'marker-icon.png')).first()[0]
                                        marker.icon_id = icon.id
                            else:
                                with self.context:
                                    icon = self.db.session.execute(self.db.session.query(Icon)
                                                                   .filter(Icon.filename == 'marker-icon.png')).first()[0]
                                    marker.icon_id = icon.id

                        if 'altsrc' in tag.attrs:
                            marker.location_source = tag.attrs['altsrc']

                link = event.find('link')
                if link:
                    marker.parent_callsign = link.attrs['parent_callsign'] if 'parent_callsign' in link.attrs else None
                    marker.production_time = link.attrs['production_time'] if 'production_time' in link.attrs else None
                    marker.relation = link.attrs['relation'] if 'relation' in link.attrs else None
                    marker.relation_type = link.attrs['relation_type'] if 'relation_type' in link.attrs else None
                    marker.parent_uid = link.attrs['uid'] if 'uid' in link.attrs else None

                marker.point_id = point_pk
                marker.cot_id = cot_pk

                with self.context:
                    try:
                        self.db.session.add(marker)
                        self.db.session.commit()
                        self.logger.debug('added marker')
                    except exc.IntegrityError:
                        self.db.session.rollback()
                        self.db.session.execute(
                            update(Marker).where(Marker.uid == marker.uid).values(point_id=marker.point_id,
                                                                                  icon_id=marker.icon_id,
                                                                                  **marker.serialize()))
                        self.db.session.commit()
                        self.logger.debug('updated marker')

                    point = self.db.session.execute(self.db.session.query(Point).filter(Point.id == point_pk)).first()[0]

                    serialized = marker.to_json()
                    serialized['point'] = point.to_json()
                    serialized['icon'] = icon.serialize() if icon else None
                    socketio.emit('marker', serialized, namespace='/socket.io')

            except BaseException as e:
                self.logger.error("Failed to parse marker: {}".format(e))
                self.logger.error(traceback.format_exc())

    def parse_rbline(self, event, uid, point_pk, cot_pk):
        if re.match("^u-rb", event.attrs['type']):
            self.logger.debug("Got an R&B line")
            rb_line = RBLine()

            detail = event.find('detail')
            if detail:
                rb_line.uid = event.attrs['uid']
                rb_line.sender_uid = uid
                rb_line.timestamp = datetime_from_iso8601_string(event.attrs['start'])
                rb_line.point_id = point_pk
                rb_line.cot_id = cot_pk

                for tag in detail:
                    if tag.name == 'range':
                        rb_line.range = tag.attrs['value']
                    if tag.name == 'bearing':
                        rb_line.bearing = tag.attrs['value']
                    if tag.name == 'inclination':
                        rb_line.inclination = tag.attrs['value']
                    if tag.name == 'anchorUID':
                        rb_line.anchor_uid = tag.attrs['value']
                    if tag.name == 'rangeUnits':
                        rb_line.range_units = tag.attrs['value']
                    if tag.name == 'bearingUnits':
                        rb_line.bearing_units = tag.attrs['value']
                    if tag.name == 'northRef':
                        rb_line.north_ref = tag.attrs['value']
                    if tag.name == 'color':
                        rb_line.color = tag.attrs['value']
                        rb_line.color_hex = rb_line.color_to_hex()
                    if tag.name == 'contact':
                        rb_line.callsign = tag.attrs['callsign']
                    if tag.name == 'strokeColor':
                        rb_line.stroke_color = tag.attrs['value']
                    if tag.name == 'strokeWeight':
                        rb_line.stroke_weight = tag.attrs['value']
                    if tag.name == 'labels_on':
                        rb_line.labels_on = (tag.attrs['value'] == 'true')

                with self.context:

                    start_point = \
                        self.db.session.execute(self.db.session.query(Point).filter(Point.id == point_pk)).first()[0]
                    end_point = rb_line.calc_end_point(start_point)
                    rb_line.end_latitude = end_point['latitude']
                    rb_line.end_longitude = end_point['longitude']

                    try:
                        self.db.session.add(rb_line)
                        self.db.session.commit()
                        self.logger.debug("Inserted new R&B line: {}".format(rb_line.uid))
                    except exc.IntegrityError:
                        self.db.session.rollback()
                        self.db.session.execute(update(RBLine).where(RBLine.uid == rb_line.uid)
                                                .values(**rb_line.serialize()))
                        self.db.session.commit()
                        self.logger.debug('Updated R&B line: {}'.format(rb_line.uid))

                    rb_line.point = start_point
                    socketio.emit("rb_line", rb_line.to_json(), namespace='/socket.io')

    def rabbitmq_routing(self, event, data):
        # RabbitMQ Routing
        chat = event.find("__chat")
        destinations = event.find_all('dest')

        if chat and 'chatroom' in chat.attrs and chat.attrs['chatroom'] == 'All Chat Rooms':
            self.rabbit_channel.basic_publish(exchange='chatrooms', routing_key='All Chat Rooms', body=json.dumps(data))

        elif destinations:
            for destination in destinations:
                self.rabbit_channel.basic_publish(exchange='dms',
                                                  routing_key=self.online_callsigns[destination.attrs['callsign']]['uid'],
                                                  body=json.dumps(data))

        # If no destination or callsign is specified, broadcast to all TAK clients
        elif self.rabbit_channel and self.rabbit_channel.is_open:
            self.rabbit_channel.basic_publish(exchange='cot', routing_key="", body=json.dumps(data))

        # Do nothing because the RabbitMQ channel hasn't opened yet or has closed
        else:
            self.logger.debug("Not publishing, channel closed")

    def get_affiliation(self, type):
        if re.match("^t-", type):
            return self.get_tasking(type)
        if re.match("^a-f-", type):
            return "friendly"
        if re.match("^a-h-", type):
            return "hostile"
        if re.match("^a-u-", type):
            return "unknown"
        if re.match("^a-p-", type):
            return "pending"
        if re.match("^a-a-", type):
            return "assumed"
        if re.match("^a-n-", type):
            return "neutral"
        if re.match("^a-s-", type):
            return "suspect"
        if re.match("^a-j-", type):
            return "joker"
        if re.match("^a-k-", type):
            return "faker"

    def get_tasking(self, type):
        if re.match("^t-x-f", type):
            return "remarks"
        if re.match("^t-x-s", type):
            return "state/sync"
        if re.match("^t-s", type):
            return "required"
        if re.match("^t-z", type):
            return "cancel"
        if re.match("^t-x-c-c", type):
            return "commcheck"
        if re.match("^t-x-c-g-d", type):
            return "dgps"
        if re.match("^t-k-d", type):
            return "destroy"
        if re.match("^t-k-i", type):
            return "investigate"
        if re.match("^t-k-t", type):
            return "target"
        if re.match("^t-k", type):
            return "strike"
        if re.match("^t-", type):
            return "tasking"

    def get_battle_dimension(self, type):
        if re.match("^a-.-A", type):
            return "airborne"
        if re.match("^a-.-G", type):
            return "ground"
        if re.match("^a-.-G-I", type):
            return "installation"
        if re.match("^a-.-S", type):
            return "surface/sea"
        if re.match("^a-.-U", type):
            return "subsurface"

    def parse_type(self, type):
        if re.match("^a-.-G-I", type):
            return "installation"
        if re.match("^a-.-G-E-V", type):
            return "vehicle"
        if re.match("^a-.-G-E", type):
            return "equipment"
        if re.match("^a-.-A-W-M-S", type):
            return "sam"
        if re.match("^a-.-A-M-F-Q-r", type):
            return "uav"

    def on_message(self, unused_channel, basic_deliver, properties, body):
        try:
            body = json.loads(body)
            soup = BeautifulSoup(body['cot'], 'xml')
            event = soup.find('event')
            if event:
                self.parse_device_info(body['uid'], soup, event)
                cot_pk = self.insert_cot(soup, event, body['uid'])
                point_pk = self.parse_point(event, body['uid'], cot_pk)
                self.parse_geochat(event, cot_pk, point_pk)
                self.parse_video(event, cot_pk)
                self.parse_groups(event, body['uid'])
                self.parse_alert(event, body['uid'], point_pk, cot_pk)
                self.parse_casevac(event, body['uid'], point_pk, cot_pk)
                self.parse_marker(event, body['uid'], point_pk, cot_pk)
                self.parse_rbline(event, body['uid'], point_pk, cot_pk)
                self.rabbitmq_routing(event, body)

                # EUD went offline
                if event.attrs['type'] == 't-x-d-d':
                    link = event.find('link')

                    try:
                        with self.context:
                            eud = self.db.session.execute(self.db.select(EUD).filter_by(uid=body['uid'])).scalar_one()
                            eud.last_event_time = event.attrs['start']
                            eud.last_status = 'Disconnected'
                            self.db.session.commit()
                            self.logger.debug("Updated {}".format(body['uid']))
                    except BaseException as e:
                        self.logger.error("Failed to update EUD: {}".format(e))

                    self.online_euds.pop(link.attrs['uid'])
        except BaseException as e:
            self.logger.error(traceback.format_exc())
