import json
import asyncio
from typing import List, Dict
import redis.asyncio as redis

from config import config


class RedisSessionManager:
    def __init__(
        self,
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        decode_responses=True,
        username=config.REDIS_USERNAME,
        password=config.REDIS_PASSWORD,
        default_ttl: int = 900,
        debounce_ttl: int = 5
    ):
        """Initializes Async Redis connection."""
        self.r = redis.Redis(
            host=host,
            port=port,
            decode_responses=decode_responses,
            username=username,
            password=password
        )
        self.default_ttl = default_ttl
        self.debounce_ttl = debounce_ttl

        self.active_monitors = set()

    def _format_key(self, phone: str) -> str:
        clean_phone = "".join(filter(str.isdigit, phone))
        return f"session:{clean_phone}"
        
    def _format_buffer_key(self, phone: str) -> str:
        clean_phone = "".join(filter(str.isdigit, phone))
        return f"buffer:{clean_phone}"

    def _format_lock_key(self, phone: str) -> str:
        clean_phone = "".join(filter(str.isdigit, phone))
        return f"lock:{clean_phone}"

    async def stack_incoming_message(self, phone: str, text: str) -> None:
        """Pushes a raw message to the temporary buffer and resets the typing lock."""
        buffer_key = self._format_buffer_key(phone)
        lock_key = self._format_lock_key(phone)

        pipe = self.r.pipeline()
        pipe.rpush(buffer_key, text)
        pipe.setex(lock_key, self.debounce_ttl, "is_typing")
        await pipe.execute()

    async def get_and_clear_buffer(self, phone: str) -> str:
        """Fetches all messages in the buffer, joins them, and clears the buffer."""
        buffer_key = self._format_buffer_key(phone)

        messages = await self.r.lrange(buffer_key, 0, -1)
        
        if messages:
            await self.r.delete(buffer_key)
            return "\n".join(messages)
        return ""

    async def monitor_typing_lock(self, phone: str, dispatch_callback):
        """
        Background loop to check if the user stopped typing.
        When the lock expires, it triggers the callback function (e.g., Celery).
        """
        lock_key = self._format_lock_key(phone)
        
        try:
            while True:
                await asyncio.sleep(1)
                
                is_locked = await self.r.exists(lock_key)
                if not is_locked:
                    aggregated_text = await self.get_and_clear_buffer(phone)
                    
                    if aggregated_text:
                        await self.append_client_message(phone, aggregated_text)

                        await dispatch_callback(phone, aggregated_text)
                        
                    break
        finally:
            self.active_monitors.discard(phone)

    async def append_client_message(self, phone: str, text: str) -> None:
        key = self._format_key(phone)
        payload = json.dumps({"role": "user", "content": text})

        pipe = self.r.pipeline()
        pipe.rpush(key, payload)
        pipe.expire(key, self.default_ttl)
        await pipe.execute()

    async def get_conversation_history(self, phone: str) -> List[Dict[str, str]]:
        key = self._format_key(phone)
        raw_messages = await self.r.lrange(key, 0, -1)
        return [json.loads(msg) for msg in raw_messages]

    async def delete_session(self, phone: str) -> bool:
        key = self._format_key(phone)
        return bool(await self.r.delete(key))

    async def has_active_session(self, phone: str) -> bool:
        key = self._format_key(phone)
        return bool(await self.r.exists(key))

    async def rehydrate_session(self, phone: str, messages: List[Dict[str, str]]) -> None:
        if not messages:
            return
        key = self._format_key(phone)
        
        pipe = self.r.pipeline()
        pipe.delete(key)  
        for msg in messages:
            pipe.rpush(key, json.dumps(msg))
        pipe.expire(key, self.default_ttl)
        await pipe.execute()

redis_manager = RedisSessionManager()