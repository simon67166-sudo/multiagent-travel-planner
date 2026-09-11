"""
达人社区内容 + 历史记录存储模块

范围说明：
- 达人社区内容用 Chroma 向量库存，支持"两阶段检索"第一阶段
  （按人格向量相似度筛选，见 agent-architecture.md 4.2）。
  第二阶段（候选集内部按内容相关性排序）留给调用方自己实现，具体排序算法待定。
- persona 向量本身怎么算（人格系统）还没定义，这里只负责存和查，
  调用方传入现成的向量，不关心向量是怎么生成的。
- 历史记录（行程完成后的反馈，用于校准人格，见 4.4）目前只是结构化持久化，
  先不做向量检索，用一个 JSON Lines 文件追加存储。

图文帖子存储约定（队友插入模拟数据时按这个来）：
- 帖子正文/结构化字段存进 Chroma 的 metadata，字段见 add_post() 的 content 参数说明。
- 图片二进制不进 Chroma（向量库不适合存大文件），本地落盘在 data/images/<post_id>/ 下，
  content["images"] 里只存相对路径字符串列表，用 save_image() 写图会自动生成这个相对路径。
  以后真的要上线部署，把 save_image()/resolve_image_path() 换成对象存储(OSS/CDN) 的
  上传/取 URL 逻辑就行，上层调用方式不用变。

依赖：pip install chromadb
"""

import json
import time
from pathlib import Path
from typing import Any

import chromadb

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_IMAGES_DIR = _DATA_DIR / "images"
_IMAGES_DIR.mkdir(exist_ok=True)

_chroma_client = chromadb.PersistentClient(path=str(_DATA_DIR / "chroma"))
_community_collection = _chroma_client.get_or_create_collection("community_posts")

_HISTORY_FILE = _DATA_DIR / "history.jsonl"


# ---------------------------------------------------------------------------
# 图片存储 -- 本地落盘，帖子里只引用相对路径（Chroma metadata 不适合存二进制/列表）
# ---------------------------------------------------------------------------
def save_image(post_id: str, filename: str, image_bytes: bytes) -> str:
    """写入一张图片，返回存进 content["images"] 列表里用的相对路径字符串。"""
    post_dir = _IMAGES_DIR / post_id
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / filename).write_bytes(image_bytes)
    return f"images/{post_id}/{filename}"


def resolve_image_path(relative_path: str) -> Path:
    """把 content["images"] 里的相对路径转换成本地可读的绝对路径。"""
    return _DATA_DIR / relative_path


# ---------------------------------------------------------------------------
# 达人社区内容 -- 两阶段检索第一阶段：按人格向量相似度筛选
# ---------------------------------------------------------------------------
def add_post(post_id: str, persona_vector: list[float], content: dict[str, Any]) -> None:
    """
    content 字段：
      结构化字段（对齐 docs/agent-interfaces.md 达人 Agent 输出契约，用于排序/展示）：
        place, time_slot, avg_cost, rating, avoid_tips, verified_trip
      图文字段：
        caption: str              帖子正文文字，可选
        images: list[str]         图片相对路径列表，可选；用 save_image() 写图拿到路径后传进来
      港澳达人数据库导入用（见 import_hk_macau_data.py）：
        city, category, post_type: str 结构化筛选字段
        tags: list[str]           标签列表，可选，跟 images 一样序列化存
        verified_local: bool      True=本地人认证来源，查询时会被优先加权（见 query_similar_posts）
    """
    content = dict(content)
    images = content.pop("images", None) or []
    tags = content.pop("tags", None) or []
    stored = {k: v for k, v in content.items() if v is not None}
    stored["images_json"] = json.dumps(images, ensure_ascii=False)
    stored["tags_json"] = json.dumps(tags, ensure_ascii=False)
    _community_collection.upsert(
        ids=[post_id],
        embeddings=[persona_vector],
        metadatas=[stored],
    )


_VERIFIED_LOCAL_BOOST = 1.15  # 本地人认证帖子的相似度加权系数，数值后面按需调
_OVERFETCH_MULTIPLIER = 3  # 要让加权真的能把本地人帖子挤进 top_k（而不是只在候选集内部换个顺序），
# 必须比 top_k 多查一些候选再重新排序截断，不然 Chroma 已经按原始距离截到 top_k 了，加权无从谈起


def _restore_metadata(post_id: str, metadata: dict) -> dict:
    """把 Chroma 存的 metadata 还原成对外的帖子字典：反序列化 images_json/tags_json，
    补上 post_id。query_similar_posts()/find_posts_by_place() 共用，别写两遍。"""
    metadata = dict(metadata)
    images = json.loads(metadata.pop("images_json", "[]") or "[]")
    tags = json.loads(metadata.pop("tags_json", "[]") or "[]")
    return {"post_id": post_id, "images": images, "tags": tags, **metadata}


def query_similar_posts(persona_vector: list[float], top_k: int = 5, city: str | None = None) -> list[dict]:
    """
    第一阶段：按人格向量相似度找候选帖子，返回时带 similarity_score 和还原出来的 images/tags 列表。
    第二阶段的内容相关性排序留给调用方（比如达人 Agent 自己）在这个候选集里再做。

    city: 传了就只在这个城市的帖子里找（Chroma metadata 精确匹配的硬过滤，在查询阶段就生效，
    不是"先按相似度截 top_k 再过滤掉不match的"——那样会导致过滤完结果不够甚至是空的）。
    不传（默认）就跟以前一样不限城市——兼容没有 city 字段的老数据（比如杭州 demo 帖子）。

    verified_local=True 的帖子（本地人认证来源，见 import_hk_macau_data.py）会被优先加权：
    先多捞 top_k * _OVERFETCH_MULTIPLIER 个候选，按加权后的分数重新排序再截到 top_k，
    这样加权才真的能影响"谁能进 top_k"，不是只在最终结果里调换个先后顺序。
    """
    fetch_n = top_k * _OVERFETCH_MULTIPLIER
    where = {"city": city} if city else None
    result = _community_collection.query(query_embeddings=[persona_vector], n_results=fetch_n, where=where)
    posts = []
    ids = result["ids"][0]
    metadatas = result["metadatas"][0]
    distances = result["distances"][0]
    for post_id, metadata, distance in zip(ids, metadatas, distances):
        post = _restore_metadata(post_id, metadata)
        similarity_score = 1 / (1 + distance)  # 距离转相似度，公式后面按需调整
        if post.get("verified_local"):
            similarity_score *= _VERIFIED_LOCAL_BOOST
        post["similarity_score"] = similarity_score
        posts.append(post)
    posts.sort(key=lambda p: p["similarity_score"], reverse=True)
    return posts[:top_k]


def find_posts_by_place(place: str) -> list[dict]:
    """
    按地点名字精确匹配查社区帖子（Chroma metadata 过滤，不是向量相似度检索）——给达人 Agent
    的"口碑复核"用（content_agent._nearby_plan()：查真实高德 POI 之后，看看社区库里有没有人
    评价过这个地点）。找不到返回空列表，是正常情况：229 条帖子覆盖的地点有限，真实 POI 的名字
    （比如"义顺牛奶公司(新马路分店)"）跟帖子里手打的地点名字（"义顺牛奶公司"）也经常对不上，
    命中率本来就不高，调用方不能假设"没查到=这个地方不好"。

    每条额外带 persona_vector 字段（帖子当初存进去的人格向量本身，不是 metadata，要显式
    include=["embeddings"] 才拿得到）——口碑复核要拿它跟当前用户的人格向量算余弦相似度。
    """
    result = _community_collection.get(where={"place": place}, include=["metadatas", "embeddings"])
    posts = []
    for post_id, metadata, embedding in zip(result["ids"], result["metadatas"], result["embeddings"]):
        post = _restore_metadata(post_id, metadata)
        post["persona_vector"] = list(embedding) if embedding is not None else None
        posts.append(post)
    return posts


def delete_post(post_id: str) -> None:
    _community_collection.delete(ids=[post_id])


# ---------------------------------------------------------------------------
# 历史记录 -- 行程反馈日志，用于校准人格（agent-architecture.md 4.4），先不做向量检索
# ---------------------------------------------------------------------------
def log_history(user_id: str, trip_id: str, record: dict[str, Any]) -> None:
    entry = {
        "user_id": user_id,
        "trip_id": trip_id,
        "timestamp": time.time(),
        **record,
    }
    with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_history(user_id: str | None = None) -> list[dict]:
    if not _HISTORY_FILE.exists():
        return []
    records = []
    with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if user_id is None or entry.get("user_id") == user_id:
                records.append(entry)
    return records


if __name__ == "__main__":
    # 1x1 像素的最小 PNG，仅用来验证图片存取流程，不是真实图片素材
    _TINY_PNG = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000155a2415a0000000"
        "049454e44ae426082"
    )
    image_path = save_image("demo-post-1", "cover.png", _TINY_PNG)

    add_post(
        "demo-post-1",
        [0.1, 0.2, 0.3],
        {
            "place": "西湖",
            "time_slot": "上午",
            "avg_cost": 120,
            "rating": 4.7,
            "avoid_tips": "旺季排队",
            "verified_trip": True,
            "caption": "西湖边坐了一上午，人均120，出片率很高",
            "images": [image_path],
        },
    )
    add_post(
        "demo-post-2",
        [0.9, 0.8, 0.7],
        {
            "place": "灵隐寺",
            "time_slot": "下午",
            "avg_cost": 60,
            "rating": 4.5,
            "verified_trip": True,
        },
    )
    results = query_similar_posts([0.1, 0.2, 0.31], top_k=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("图片本地路径:", resolve_image_path(results[0]["images"][0]))

    log_history("demo-user", "trip-1", {"rating": 5, "comment": "还不错"})
    print(json.dumps(get_history("demo-user"), ensure_ascii=False, indent=2))
