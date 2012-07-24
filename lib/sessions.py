import json, re

from flask import g, request
from uuid import uuid4

from lib import DELETE_SESSION_PERIOD, get_time
from messages import send_message

class Session(object):

    CASE_OPTIONS = {
        'normal': 'Normal',
        'upper': 'UPPER CASE',
        'lower': 'lower case',
        'title': 'Title Case',
        'inverted': 'iNVERTED',
        'alternating': 'AlTeRnAtInG'
    }

    DEFAULTS = {
        'acronym': '??',
        'name': 'Anonymous',
        'color': '000000',
        'character': 'anonymous/other',
        'quirk_prefix': '',
        'case': 'normal',
        'replacements': '[]',
        'group': 'user'
    }

    def __init__(self, redis, session=None, chat=None):

        self.redis = redis
        self.session = session or str(uuid4())
        self.chat = chat
        self.prefix = self.chat_prefix = "session."+self.session

        chat_data = Session.DEFAULTS

        # Load global session data.
        if redis.exists(self.prefix):
            chat_data = redis.hgetall(self.chat_prefix)
        else:
            redis.hmset(self.prefix, chat_data)

        # Load chat-specific data.
        if chat is not None:
            self.chat_prefix += '.chat.'+chat
            if redis.exists(self.chat_prefix):
                chat_data = redis.hgetall(self.chat_prefix)
            else:
                redis.hmset(self.chat_prefix, chat_data)

        for attrib, value in chat_data.items():
            setattr(self, attrib, unicode(value, encoding='utf-8'))

        # XXX lazy loading on these?

        self.picky = redis.smembers(self.prefix+'.picky')

        redis.zadd('all-sessions', self.session, get_time(DELETE_SESSION_PERIOD))

    def character_dict(self, unpack_replacements=False, hide_silence=True):
        character_dict = dict((attrib, getattr(self, attrib)) for attrib in Session.DEFAULTS.keys())
        # Don't tell silenced users that they're silenced.
        if hide_silence and character_dict['group']=='silent':
            character_dict['group'] = 'user'
        if unpack_replacements:
            character_dict['replacements'] = json.loads(character_dict['replacements'])
        return character_dict

    def save(self, form):
        self.save_character(form)
        self.save_pickiness(form)

    def save_character(self, form):

        redis = self.redis
        prefix = self.prefix

        old_name = self.name
        old_acronym = self.acronym
        old_color = self.color

        # Truncate acronym to 10 characters.
        self.acronym = form['acronym'][:10]

        # Validate name
        if len(form['name'])>0:
            # Truncate name to 50 characters.
            self.name = form['name'][:50]
        else:
            raise ValueError("name")

        # Validate colour
        if re.compile('^[0-9a-fA-F]{6}$').search(form['color']):
            self.color = form['color']
        else:
            raise ValueError("color")

        # Validate character
        if form['character'] in g.redis.smembers('all-chars'):
            setattr(self, 'character', form['character'])
        else:
            raise ValueError("character")

        self.quirk_prefix = form['quirk_prefix']

        # Validate case
        if form['case'] in self.CASE_OPTIONS.keys():
            setattr(self, 'case', form['case'])
        else:
            raise ValueError("case")

        self.replacements = zip(form.getlist('quirk_from'), form.getlist('quirk_to'))
        # Strip out any rows where from is blank or the same as to.
        self.replacements = [_ for _ in self.replacements if _[0]!='' and _[0]!=_[1]]
        # And encode as JSON.
        self.replacements = json.dumps(self.replacements)

        redis.hmset(self.chat_prefix, self.character_dict(hide_silence=False))

        # Chat-related things.
        if self.chat is not None:
            g.redis.sadd('chat.'+self.chat+'.characters', g.user.character)
            if self.name!=old_name or self.acronym!=old_acronym:
                user_change_message = '%s [%s] is now %s [%s].' % (old_name, old_acronym, self.name, self.acronym)
                if self.group=='silent':
                    send_message(g.redis, request.form['chat'], -1, 'private', user_change_message, audience=self.session)
                    send_message(g.redis, request.form['chat'], -1, 'user_change', None)
                else:
                    send_message(g.redis, request.form['chat'], -1, 'user_change', user_change_message)
            elif self.color!=old_color:
                send_message(g.redis, request.form['chat'], -1, 'user_change', None)

    def save_pickiness(self, form):

        ckey = self.prefix+'.picky'
        self.redis.delete(ckey)

        if 'picky' in form:
            chars = self.picky = set(k[6:] for k in form.keys() if k.startswith('picky-'))
            if not chars:
                raise ValueError("no_characters")
            for char in self.picky:
                self.redis.sadd(ckey, char)

    def set_chat(self, chat):
        if self.chat is None:
            self.chat = chat
            self.chat_prefix = self.prefix+'.chat.'+chat
            if self.redis.exists(self.chat_prefix):
                chat_data = self.redis.hgetall(self.chat_prefix)
                for attrib, value in chat_data.items():
                    setattr(self, attrib, unicode(value, encoding='utf-8'))
            else:
                self.redis.hmset(self.chat_prefix, self.character_dict(hide_silence=False))

    def set_group(self, group):
        self.group = group
        self.redis.hset(self.chat_prefix, 'group', group)

def get_counter(chat, session):
    return g.redis.lrange('chat.'+chat+'.counter', 0, -1).index(session)
