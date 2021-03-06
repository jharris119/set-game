import cherrypy
from haikunator import haikunate
from collections import defaultdict
import json
import sys

from ws4py.websocket import WebSocket
from ws4py.messaging import TextMessage
from ws4py.server.cherrypyserver import WebSocketPlugin

from app.multiplayer import MultiplayerSet
from app.setutils import *

cherrypy._cpconfig.environments['test_suite']['log.screen'] = True


class MultiplayerWebService:
    def __init__(self):
        self.games = dict()

    # #########################################################################
    # HTTP endpoints
    # #########################################################################
    @cherrypy.expose
    @cherrypy.tools.json_out()
    def go(self, **kwargs):
        """
        Endpoint to create and/or join a MultiplayerSet game.

        Assign this user/session to a specific game.

        :param name: the name of the game to join, or None to start a new game
        :return: a dict with property `game` mapping to the name of the game
        """
        if cherrypy.session.get('game'):
            cherrypy.log('User leaving game')
            self.leave()

        name = kwargs.get('name')
        if name:
            try:
                game = self.games[name]
            except KeyError:
                raise cherrypy.HTTPError(422, 'This game does not exist.')
        else:
            name = self.make_name()
            game = self.games[name] = self.create_game()

        cherrypy.session['game'] = game

        return {
            'game': name,
            'id': cherrypy.session.id
        }

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def status(self):
        """
        Endpoint to get the games currently under management by the app.

        :return: a dict of game names mapped to player count in each game
        """
        return {name: len(game.players) for name, game in self.games.items()}

    @cherrypy.expose
    def leave(self):
        """
        Endpoint to leave the current game.
        """
        if cherrypy.session.get('game'):
            del cherrypy.session['game']

    @cherrypy.expose
    def destroy(self):
        """
        Endpoint to destroy all games, only available on the test suite.
        """
        if cherrypy.config.get('environment') == 'test_suite':
            self.games.clear()

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def find(self, **kwargs):
        """
        Endpoint to find all the sets on the board, only available on the test suite.
        """
        if cherrypy.config.get('environment') == 'test_suite':
            found = find_all_sets(self.games[kwargs.get('name')].cards)
            print([SetSerializer.to_dict(a_set) for a_set in found], file=sys.stderr)

            return json.dumps([SetSerializer.to_dict(a_set) for a_set in found])

    def create_game(self):
        """
        Creates a game.

        :return: the newly created game
        """
        return MultiplayerSet()

    def make_name(self):
        """
        Makes a Heroku-style name.

        :return: a Heroku-style haiku name
        """
        while True:
            name = haikunate()
            if name not in self.games:
                return name

    # #########################################################################
    # Websocket methods
    # #########################################################################
    @cherrypy.expose
    def ws(self, game=None, id=None):
        cherrypy.request.ws_handler.game_name = game
        cherrypy.request.ws_handler.permanent_id = id
        cherrypy.request.ws_handler.game = self.games[game]
        cherrypy.request.ws_handler.player = None
        cherrypy.log("Handler created: %s" % repr(cherrypy.request.ws_handler))


class MultiplayerWebSocket(WebSocket):
    """
    A web socket.

    This guy is the same as `cherrypy.request.ws_handler` via the
    `tools.websocket.handler_cls` directive on the `/ws` route.

    Attributes:
        game_name       the name of the game this socket is connected to
        permanent_id    the id of the player this socket is connected to
        game            the game itself that this socket is connected to
        player          the player abstraction that this socket is connected to
    """
    def opened(self):
        self.heartbeat_freq = 2.0
        cherrypy.engine.publish('add-client', self)

    def received_message(self, message):
        print(message, file=sys.stderr)
        message = json.loads(str(message))

        action = message.get('request')
        handlers = {
            'add-player': self.onAddPlayer,
            'change-name': self.onChangeName,
            'countdown-start': self.onCountdownStart,
            'verify-set': self.onVerifySet,
            'ping': (lambda x: None)
        }
        if action in handlers:
            handlers[action](message)

    # #########################################################################
    # Individual event handlers
    # #########################################################################
    def onAddPlayer(self, data):
        """
        Handler for an add-player request.

        :param data: the request data, json.load(s)-ed
        """
        response = dict(request=data['request'], id=data['id'])

        player = self.game.add_player(data.get('new_name'))
        if player:
            self.player = player
            response.update({
                'name': player.id,
                'players': {p.id: len(p.found) for p in self.game.players.values()} if player else {}
            })
        self.broadcast_as_json(response)

    def onChangeName(self, data):
        """
        Handler for a change-name request.

        :param data: the request data, json.load(s)-ed
        :param response: the response data, to be json.dump(s)-ed
        """
        response = dict(request=data['request'], id=data['id'])

        name = data.get('new_name')
        if name and name not in self.game.players:
            del self.game.players[self.player.id]

            self.player.id = name
            self.game.players[name] = self.player

            response.update({
                'name': self.player.id,
                'players': {p.id: len(p.found) for p in self.game.players.values()}
            })

        self.broadcast_as_json(response)

    def onCountdownStart(self, data):
        import time

        response = dict(request=data['request'], now=time.time())
        self.broadcast_as_json(response)
        time.sleep(10)

        if not self.game.started and len(self.game.players) > 1:
            self.game.start()
            response = dict(request='start-game')
            response.update({
                'cards': [card.to_hash() for card in self.game.cards]
            })
            self.broadcast_as_json(response)

    def onVerifySet(self, data):
        cards = [CardSerializer.from_dict(card) for card in data['cards']]
        result = self.game.receive_selection(cards, self.player)

        response = dict(request=data['request'], id=data['id'])
        response.update({
            'player': self.player.id,
            'found': len(self.player.found)
        })
        if result.valid == SetValidation['OK']:
            response.update({
                'valid': True,
                'cards_to_remove': [CardSerializer.to_dict(card) for card in result.old_cards],
                'cards_to_add': [CardSerializer.to_dict(card) for card in result.new_cards],
                'game_over': result.game_over
            })
        self.broadcast_as_json(response)

    # #########################################################################
    # Methods for broadcasting out across web sockets
    # #########################################################################
    def broadcast_as_json(self, message):
        """
        Dump a message thingy to JSON and broadcast it.

        :param message: the message
        """
        self.broadcast(json.dumps(message))

    def broadcast(self, message):
        """
        Broadcast a message to all connected web sockets.

        :param message: the raw message
        """
        for ws in self.websockets().values():
            ws.send(message)

    def closed(self, code, reason="A client left the room without a proper explanation."):
        ws = self.websockets()
        del ws[self.permanent_id]
        if len(ws) == 0:
            cherrypy.engine.publish('del-client', self.game_name)
        cherrypy.engine.publish('websocket-broadcast', TextMessage(reason))

    def websockets(self):
        """
        Gets all of the websockets attached to the same game as this socket.

        :return: a list of WebSockets
        """
        return cherrypy.engine.publish('get-client').pop().get(self.game_name, list())


class MultiplayerWebSocketPlugin(WebSocketPlugin):
    def __init__(self, bus):
        WebSocketPlugin.__init__(self, bus)
        self.clients = defaultdict(dict)

    def start(self):
        WebSocketPlugin.start(self)
        self.bus.subscribe('add-client', self.add_client)
        self.bus.subscribe('get-client', self.get_client)
        self.bus.subscribe('del-client', self.del_client)

    def stop(self):
        WebSocketPlugin.stop(self)
        self.bus.unsubscribe('add-client', self.add_client)
        self.bus.unsubscribe('get-client', self.get_client)
        self.bus.unsubscribe('del-client', self.del_client)

    def add_client(self, websocket):
        name = websocket.game_name
        permanent_id = websocket.permanent_id
        self.clients[name][permanent_id] = websocket

    def get_client(self):
        return self.clients

    def del_client(self, game):
        del self.clients[game]
