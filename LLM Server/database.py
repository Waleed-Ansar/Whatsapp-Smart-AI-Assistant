from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta
from typing import List, Dict
from pymongo.errors import OperationFailure

from config import config


class DatabaseManager:
    def __init__(self):
        """Initializes the Async MongoDB client and sets the database references."""
        self.client = AsyncIOMotorClient(config.MONGODB_URI)
        self.db = self.client[config.DB_NAME]
        self.collection = self.db.get_collection(config.COLLECTION_NAME)

    async def setup_indexes(self) -> None:
        """Ensures TTL and compound indexes exist for performance and auto-cleanup."""
        
        # 1. Attempt to create the new 7-day TTL Index
        try:
            await self.collection.create_index([("created_at", 1)], expireAfterSeconds=604800)
        except OperationFailure as e:
            # Code 85 means the index exists but with different options (our old 30-day rule)
            if e.code == 85: 
                print("Index conflict detected. Updating TTL from 30 days to 7 days...")
                await self.collection.drop_index("created_at_1")
                await self.collection.create_index([("created_at", 1)], expireAfterSeconds=604800)
            else:
                raise e # If it's a different database error, we still want to know about it!
        
        # 2. Compound Index (This one won't conflict)
        await self.collection.create_index([("number", 1), ("expires_at", 1)])
        print("MongoDB indexes verified and ready for 7-day auto-deletion.")

    async def save_message_to_window(self, phone: str, role: str, text: str) -> None:
        """Appends a message to the active 24h window, or creates it if it doesn't exist."""
        now = datetime.utcnow()
        
        await self.collection.update_one(
            {
                "number": phone,
                "expires_at": {"$gt": now}
            },
            {
                "$push": {"messages": {"role": role, "content": text}},
                "$setOnInsert": {
                    "created_at": now,
                    "expires_at": now + timedelta(hours=24)
                }
            },
            upsert=True
        )

    async def get_recent_messages(self, phone: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        Fetches the last N messages across ALL recent 24-hour windows.
        This allows the AI to remember context from 'yesterday' even if the old window expired.
        """
        # 1. Fetch the 3 most recent 24-hour windows for this number, sorted newest to oldest
        cursor = self.collection.find({"number": phone}).sort("created_at", -1).limit(3)
        recent_docs = await cursor.to_list(length=3)
        
        if not recent_docs:
            return []
            
        all_messages = []
        
        # 2. Reverse the documents so they are in chronological order (oldest -> newest)
        for doc in reversed(recent_docs):
            all_messages.extend(doc.get("messages", []))
            
        # 3. Return only the last N messages to keep the LLM context window clean
        return all_messages[-limit:]
        
    def close(self) -> None:
        """Closes the MongoDB connection."""
        self.client.close()

# Instantiate the singleton so it can be imported across the app
db_manager = DatabaseManager()
