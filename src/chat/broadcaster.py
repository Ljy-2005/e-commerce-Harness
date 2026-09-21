"""WebSocket 消息广播器"""


from fastapi import WebSocket


class Broadcaster:
    """管理 WebSocket 连接，广播群聊消息"""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}  # session_id → [ws, ...]

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        if session_id not in self._connections:
            self._connections[session_id] = []
        self._connections[session_id].append(ws)

    def disconnect(self, session_id: str, ws: WebSocket):
        if session_id in self._connections:
            self._connections[session_id] = [
                c for c in self._connections[session_id] if c != ws
            ]
            if not self._connections[session_id]:
                del self._connections[session_id]

    async def broadcast(self, session_id: str, message: dict):
        """向所有订阅该 session 的客户端推送消息"""
        if session_id not in self._connections:
            return
        # 取快照避免迭代中 mutation
        dead = []
        for ws in list(self._connections[session_id]):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)
