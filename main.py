import asyncio
import logging
import os
import json
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, select
from sqlalchemy.orm import sessionmaker, relationship
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai_handler import create_thread, create_run, add_user_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

active_manager_chats = set()


DATABASE_URL = os.environ.get("DATABASE_URL")
OPENAI_KEY = os.getenv("OPENAI_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set!")

engine = create_async_engine(DATABASE_URL, echo=True)
Base = declarative_base()
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, unique=True, index=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    threads = relationship("Thread", back_populates="user", cascade="all, delete")
    openai_thread = relationship("OpenAIThread", back_populates="user", uselist=False)

class Thread(Base):
    __tablename__ = "threads"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(hours=1))
    messages = relationship("Message", back_populates="thread", cascade="all, delete")
    user = relationship("User", back_populates="threads")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("threads.id"))
    sender = Column(String)
    content = Column(Text)
    timestamp = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(hours=1))
    thread = relationship("Thread", back_populates="messages")

class OpenAIThread(Base):
    __tablename__ = "openai_threads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    openai_thread_id = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(hours=1))

    user = relationship("User", back_populates="openai_thread")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_db():
    async with async_session() as session:
        yield session

@app.on_event("startup")
async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized (if not existed).")

class UserConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
    async def connect(self, chat_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[chat_id] = websocket
        logger.info("User WS connected for chat_id=%s", chat_id)
    def disconnect(self, chat_id: str):
        if chat_id in self.active_connections:
            del self.active_connections[chat_id]
            logger.info("User WS disconnected for chat_id=%s", chat_id)
    async def send_personal_message(self, message: str, chat_id: str):
        websocket = self.active_connections.get(chat_id)
        if websocket:
            await websocket.send_text(message)

user_manager = UserConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    chat_id = websocket.query_params.get("chat_id")
    if not chat_id:
        await websocket.close(code=1008)
        logger.warning("Closed user WS (no chat_id provided).")
        return

    await user_manager.connect(chat_id, websocket)

    async with db.begin():
        stmt = select(User).where(User.chat_id == chat_id)
        result = await db.execute(stmt)
        user = result.scalars().first()

        if not user:
            user = User(chat_id=chat_id)
            db.add(user)
            await db.flush()

            thread = Thread(user_id=user.id)
            db.add(thread)

            logger.info("New user created for chat_id=%s", chat_id)
        else:
            stmt = (
                select(Thread)
                .where(Thread.user_id == user.id)
                .order_by(Thread.created_at.desc())
            )
            result = await db.execute(stmt)
            thread = result.scalars().first()
            if not thread:
                thread = Thread(user_id=user.id)
                db.add(thread)

            logger.info("Existing user for chat_id=%s, using thread id=%s", chat_id, thread.id)

    await db.commit()

    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            message_text = payload.get("message", "")

            async with db.begin():
                user_msg = Message(
                    thread_id=thread.id,
                    sender="user",
                    content=message_text
                )
                db.add(user_msg)
            await db.commit()

            broadcast_payload = {
                "chat_id": chat_id,
                "type": "message",
                "message": message_text,
                "sender": "user"
            }
            await manager_manager.broadcast(json.dumps(broadcast_payload))
            if chat_id not in active_manager_chats:
                if OPENAI_KEY and ASSISTANT_ID:
                    try:
                        async with db.begin():
                            stmt = select(OpenAIThread).where(OpenAIThread.user_id == user.id)
                            result = await db.execute(stmt)
                            openai_thread = result.scalars().first()

                            if not openai_thread:
                                openai_thread_id = await create_thread(message_text)
                                openai_thread = OpenAIThread(
                                    user_id=user.id,
                                    openai_thread_id=openai_thread_id
                                )
                                db.add(openai_thread)
                                logger.info("Создан OpenAI Thread с id=%s для user=%s", openai_thread_id, user.id)
                            else:
                                await add_user_message(openai_thread.openai_thread_id, message_text)

                        await db.commit()

                        async with db.begin():
                            bot_response = await create_run(openai_thread.openai_thread_id)
                            logger.info(f"OpenAI ответ: {bot_response}")

                            bot_msg = Message(
                                thread_id=thread.id,
                                sender="bot",
                                content=bot_response
                            )
                            db.add(bot_msg)
                        await db.commit()
                    except Exception as e:
                        logging.info(e)
                        bot_response = f"Openai error: {e}."

                    response_payload = {
                        "type": "message",
                        "message": bot_response,
                        "sender": "bot"
                    }
                    await user_manager.send_personal_message(json.dumps(response_payload), chat_id)

                    broadcast_payload_bot = {
                        "chat_id": chat_id,
                        "type": "message",
                        "message": bot_response,
                        "sender": "bot"
                    }
                    await manager_manager.broadcast(json.dumps(broadcast_payload_bot))
                else:
                    await asyncio.sleep(4)
                    bot_response = f"Bot echo: {message_text}"
                    async with db.begin():
                        bot_msg = Message(thread_id=thread.id, sender="bot", content=bot_response)
                        db.add(bot_msg)
                    await db.commit()

                    response_payload = {
                        "type": "message",
                        "message": bot_response,
                        "sender": "bot"
                    }
                    await user_manager.send_personal_message(json.dumps(response_payload), chat_id)

                    broadcast_payload_bot = {
                        "chat_id": chat_id,
                        "type": "message",
                        "message": bot_response,
                        "sender": "bot"
                    }
                    await manager_manager.broadcast(json.dumps(broadcast_payload_bot))



    except WebSocketDisconnect:
        user_manager.disconnect(chat_id)
    except Exception as e:
        user_manager.disconnect(chat_id)
        logger.exception("Unexpected error in user websocket: %s", e)


class ManagerConnectionManager:
    def __init__(self):
        self.active_connections = []
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("Manager WS connected")
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("Manager WS disconnected")
    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.exception("Error broadcasting to manager: %s", e)

manager_manager = ManagerConnectionManager()

@app.websocket("/manager/ws")
async def manager_websocket_endpoint(websocket: WebSocket):
    await manager_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            logger.info("Received from manager WS: %s", data)
    except WebSocketDisconnect:
        manager_manager.disconnect(websocket)
    except Exception as e:
        manager_manager.disconnect(websocket)
        logger.exception("Unexpected error in manager WS: %s", e)


@app.post("/manager/send")
async def send_manager_message(payload: dict, db: AsyncSession = Depends(get_db)):
    chat_id = payload.get("chat_id")
    message = payload.get("message")
    action = payload.get("action", None)
    manager_status = payload.get("managerStatus", None)

    if not chat_id or not message:
        raise HTTPException(status_code=400, detail="chat_id and message are required")

    if manager_status is not None:
        if manager_status:
            active_manager_chats.add(chat_id)
            logger.info("Manager joined chat_id=%s", chat_id)
        else:
            active_manager_chats.discard(chat_id)
            logger.info("Manager left chat_id=%s", chat_id)

    async with db.begin():
        stmt = select(User).where(User.chat_id == chat_id)
        result = await db.execute(stmt)
        user = result.scalars().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        stmt = select(Thread).where(Thread.user_id == user.id).order_by(Thread.created_at.desc())
        result = await db.execute(stmt)
        thread = result.scalars().first()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        msg = Message(thread_id=thread.id, sender="manager" if action is None else "action", content=message)
        db.add(msg)
    await db.commit()
    if not action:
        response_payload = {"type": "message", "message": message, "sender": "manager"}
    else:
        response_payload = {"type": "message", "message": message, "sender": "action"}

    await user_manager.send_personal_message(json.dumps(response_payload), chat_id)
    await manager_manager.broadcast(json.dumps({
        "chat_id": chat_id,
        "type": "message",
        "message": message,
        "sender": "manager " if action is None else "action"
    }))
    return JSONResponse(content={"status": "ok"})


@app.get("/history")
async def get_chat_history(chat_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.chat_id == chat_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        return []
    stmt = select(Thread).where(Thread.user_id == user.id).order_by(Thread.created_at.desc())
    result = await db.execute(stmt)
    thread = result.scalars().first()
    if not thread:
        return []
    stmt = select(Message).where(Message.thread_id == thread.id).order_by(Message.timestamp.asc())
    result = await db.execute(stmt)
    messages_list = result.scalars().all()
    history = [
        {"sender": msg.sender, "text": msg.content, "timestamp": msg.timestamp.isoformat()}
        for msg in messages_list
    ]
    return history

@app.get("/manager/chats")
async def get_manager_chats(db: AsyncSession = Depends(get_db)):
    stmt = select(User)
    result = await db.execute(stmt)
    users = result.scalars().all()
    chats = []
    for user in users:
        stmt = select(Thread).where(Thread.user_id == user.id).order_by(Thread.created_at.desc())
        result = await db.execute(stmt)
        thread = result.scalars().first()
        if thread:
            stmt = select(Message).where(Message.thread_id == thread.id).order_by(Message.timestamp.asc())
            result = await db.execute(stmt)
            messages_list = result.scalars().all()
            history = [
                {"sender": msg.sender, "text": msg.content, "timestamp": msg.timestamp.isoformat()}
                for msg in messages_list
            ]
            chats.append({
                "id": user.chat_id,
                "userName": user.chat_id[-6:],
                "messages": history
            })
    chats.sort(key=lambda chat: chat["messages"][-1]["timestamp"] if chat["messages"] else "", reverse=True)
    return chats
