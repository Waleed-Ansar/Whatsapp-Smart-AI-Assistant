import json
import redis
from typing import List, Dict

from config import config


class RedisSessionManager:
    def __init__(self,
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            decode_responses=True,
            username=config.REDIS_USERNAME,
            password=config.REDIS_PASSWORD,
            default_ttl: int = 900
        ):
        """
        Initializes Redis connection using a connection string.
        """
        # from_url automatically parses host, port, user, password, and DB
        self.r = redis.Redis(host=host, port=port, decode_responses=decode_responses, username=username, password=password)
        self.default_ttl = default_ttl

    def _format_key(self, phone: str) -> str:
        clean_phone = "".join(filter(str.isdigit, phone))
        return f"session:{clean_phone}"

    def append_client_message(self, phone: str, text: str) -> None:
        key = self._format_key(phone)
        payload = json.dumps({"role": "user", "content": text})

        pipe = self.r.pipeline()
        pipe.rpush(key, payload)
        pipe.expire(key, self.default_ttl)
        pipe.execute()

    def get_conversation_history(self, phone: str) -> List[Dict[str, str]]:
        key = self._format_key(phone)
        raw_messages = self.r.lrange(key, 0, -1)
        return [json.loads(msg) for msg in raw_messages]

redis_manager = RedisSessionManager()