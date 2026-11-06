from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI()


class UserCreate(BaseModel):
    name: str


@app.get("/users/{user_id}")
def get_user(user_id: int, verbose: bool = False):
    return {"id": user_id, "verbose": verbose}


@app.post("/users")
def create_user(payload: UserCreate):
    return payload
