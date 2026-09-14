from dotenv import load_dotenv
import os

load_dotenv()

class Config:
    LLM_API_URL = os.getenv("LLM_API_URL", "")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "")
    
    MCP_SERVER_API_KEY = os.getenv("MCP_SERVER_API_KEY", "")
    MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "")
    MCP_SERVER_TRANSPORT = "sse"

config = Config()