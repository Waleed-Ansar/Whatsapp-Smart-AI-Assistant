from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from datetime import datetime


# Initialize your Tool Server
mcp = FastMCP("RealEstateCopilot")

# ==========================================
# MCP TOOLS (Visible to the LLM)
# ==========================================
@mcp.tool()
async def get_date() -> str:
    """Fetches date."""
    date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Current date and time: {date}"

# ==========================================
# CUSTOM HTTP ROUTES (Invisible to the LLM)
# ==========================================
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """
    Standard HTTP endpoint. The LLM cannot see or trigger this.
    Use this for Docker health checks, AWS Target Groups, or UptimeRobot.
    """
    try:
        # 1. Check your Vector DB (e.g., Pinecone/Milvus)
        db_status = "ok" # Replace with actual await check_db()
        
        # 2. Check Meta Graph API connectivity
        whatsapp_api_status = "ok" # Replace with actual await check_meta_api()
        
        # 3. Check any internal CRMs
        crm_status = "ok"
        
        return JSONResponse({
            "status": "healthy",
            "services": {
                "vector_db": db_status,
                "whatsapp_api": whatsapp_api_status,
                "crm": crm_status
            }
        }, status_code=200)
        
    except Exception as e:
        return JSONResponse({
            "status": "unhealthy",
            "error": str(e)
        }, status_code=503)

if __name__ == "__main__":
    # Runs the server on http://0.0.0.0:8000
    mcp.run(transport="sse", host="0.0.0.0", port=8001)