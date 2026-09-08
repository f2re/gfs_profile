"""Owner-bound durable WN3 callbacks. Only locations/settings, never model runs."""
from __future__ import annotations
from contextlib import closing
import json
import secrets
import sqlite3
import time
from pathlib import Path

from geocode import GeoPoint
from .contracts import UiButton, UiKeyboard
from .callback_codec import decode_callback


class CardStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute('CREATE TABLE IF NOT EXISTS wn3_cards(token TEXT PRIMARY KEY, platform TEXT, user_id TEXT, chat_id TEXT, body TEXT, expires REAL)')
        return connection

    def save(self, event, point, params):
        token = secrets.token_hex(8)
        body = {'point': {'lat': point.lat, 'lon': point.lon, 'label': point.label, 'source': getattr(point, 'source', 'wn3')},
                'params': {k: v for k, v in params.items() if k not in {'run', 'cycle', 'init_time'}}}
        now = time.time()
        with closing(self._connect()) as conn, conn:
            conn.execute('DELETE FROM wn3_cards WHERE expires < ?', (now,))
            conn.execute('INSERT INTO wn3_cards VALUES(?,?,?,?,?,?)',
                         (token, event.platform, str(event.user_id), str(event.chat_id), json.dumps(body, ensure_ascii=False), now+172800))
            conn.execute('DELETE FROM wn3_cards WHERE token IN (SELECT token FROM wn3_cards WHERE platform=? AND user_id=? AND chat_id=? ORDER BY expires DESC LIMIT -1 OFFSET 100)',
                         (event.platform, str(event.user_id), str(event.chat_id)))
        return token

    def get(self, event, token):
        with closing(self._connect()) as conn, conn:
            row = conn.execute('SELECT body FROM wn3_cards WHERE token=? AND platform=? AND user_id=? AND chat_id=? AND expires>=?',
                               (token, event.platform, str(event.user_id), str(event.chat_id), time.time())).fetchone()
        if row is None:
            raise ValueError('Кнопка устарела или принадлежит другому пользователю. Откройте /wn3.')
        body = json.loads(row[0])
        p = body['point']
        return GeoPoint(float(p['lat']), float(p['lon']), p['label'], p['source']), body['params']

    @staticmethod
    def payload(token, action, value=None):
        result = f'w3|{token}|{action}' + (f'|{value}' if value is not None else '')
        if len(result.encode()) > 64:
            raise ValueError('Слишком длинный callback WN3')
        return result

    def keyboard(self, token, keyboard):
        rows = []
        for row in keyboard.rows:
            buttons = []
            for button in row:
                if button.action == 'callback':
                    data = decode_callback(button.payload)
                    if data.scope == 'wn3':
                        button = UiButton(button.text, 'callback', self.payload(token, data.action, data.value))
                buttons.append(button)
            rows.append(buttons)
        return UiKeyboard.from_rows(rows)
