from dotenv import load_dotenv
import os

load_dotenv()

class Config:
    LLM_API_URL = os.getenv("LLM_API_URL", "")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "")
    
    WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    
    SERVER_HEALTH_CHECK_URL = os.getenv("SERVER_HEALTH_CHECK_URL", "")
    MCP_SERVER_API_KEY = os.getenv("MCP_SERVER_API_KEY", "")
    MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "")
    MCP_SERVER_TRANSPORT = "sse"
    
    REDIS_HOST= os.getenv("REDIS_HOST", "")
    REDIS_PORT= int(os.getenv("REDIS_PORT", ""))
    REDIS_PASSWORD= os.getenv("REDIS_PASSWORD", "")
    REDIS_USERNAME= os.getenv("REDIS_USERNAME", "")
    
    MONGODB_URI = os.getenv("MONGODB_URI", "")
    DB_NAME = os.getenv("DB_NAME", "")
    CHAT_COLLECTION_NAME = os.getenv("CHAT_COLLECTION_NAME", "")
    CRED_COLLECTION_NAME = os.getenv("CRED_COLLECTION_NAME", "")
    
    WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
    PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
    RECIPIENT_PHONE = os.getenv("RECIPIENT_PHONE", "")

config = Config()