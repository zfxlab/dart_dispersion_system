"""Strict YAML and atomic, UTF-8 persistence."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import yaml


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"配置中存在重复键 / Tag ID: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def read_yaml(path):
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.load(f, Loader=UniqueLoader)
    if not isinstance(data, dict):
        raise ValueError("YAML 顶层必须是映射")
    return data


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_yaml(path, data):
    atomic_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
