from fastapi import FastAPI
from models import DualKeyProjects

app = FastAPI()

@app.post("/me")
def me(query: DualKeyProjects):
    return {"Result": query}