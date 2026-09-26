import json
from pathlib import Path
from ..models.session import Session
from ..models.shot import ShotRecord
from .yaml_io import atomic_text


class SessionRepository:
    @staticmethod
    def save(path, session):
        atomic_text(path, json.dumps(session.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))

    @staticmethod
    def load(path):
        with Path(path).open(encoding="utf-8") as f:
            d = json.load(f)
        if d.get("schema_version") != 1:
            raise ValueError("不支持的 Session 版本")
        shots = [ShotRecord(**s) for s in d.pop("shots")]
        ids = [s.shot_id for s in shots]
        if len(ids) != len(set(ids)):
            raise ValueError("Session 存在重复 shot_id")
        session = Session(**d, shots=shots)
        if type(session.next_shot_id) is not int or session.next_shot_id <= max(ids, default=0):
            raise ValueError("Session 的 next_shot_id 非法")
        return session
