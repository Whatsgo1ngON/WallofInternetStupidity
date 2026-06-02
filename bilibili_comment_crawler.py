#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
爬取 B 站指定视频/动态的评论及评论回复，并保存为 JSON。

示例：
  python bilibili_comment_crawler.py --video BV1xx411c7mD --dynamic 1234567890123456789 -o comments.json
  python bilibili_comment_crawler.py --targets targets.json -o comments.json --workers 8 --cookie "SESSDATA=..."

targets.json 示例：
[
  {"type": "video", "id": "BV1xx411c7mD"},
  {"type": "dynamic", "id": "1234567890123456789"}
]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


API_REPLY = "https://api.bilibili.com/x/v2/reply"
API_REPLY_WBI_MAIN = "https://api.bilibili.com/x/v2/reply/wbi/main"
API_REPLY_REPLY = "https://api.bilibili.com/x/v2/reply/reply"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_DYNAMIC_DETAIL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"

TYPE_VIDEO = 1
TYPE_DYNAMIC = 17

WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


@dataclass(frozen=True)
class Target:
    kind: str
    raw_id: str
    oid: int
    type_code: int


class BilibiliClient:
    def __init__(
        self,
        cookie: str = "",
        timeout: float = 15.0,
        retries: int = 3,
        min_delay: float = 0.2,
        max_delay: float = 0.8,
    ) -> None:
        self.cookie = cookie.strip()
        self.timeout = timeout
        self.retries = retries
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._wbi_mixin_key: str | None = None

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        full_url = f"{url}?{urlencode(params)}"
        for attempt in range(1, self.retries + 1):
            if self.max_delay > 0:
                time.sleep(random.uniform(self.min_delay, self.max_delay))

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
            }
            if self.cookie:
                headers["Cookie"] = self.cookie

            try:
                with urlopen(Request(full_url, headers=headers), timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    data = json.loads(body)
                    if data.get("code") in (-352, -412, -509) and attempt < self.retries:
                        time.sleep((2 * attempt) + random.uniform(0.5, 1.5))
                        continue
                    return data
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(1.5 * attempt)

        raise RuntimeError(f"request failed after {self.retries} retries: {full_url}: {last_error}")

    def resolve_video_oid(self, raw: str) -> int:
        video_id = normalize_video_id(raw)
        if video_id.lower().startswith("av"):
            return int(video_id[2:])
        if video_id.isdigit():
            return int(video_id)

        data = self.get_json(API_VIEW, {"bvid": video_id})
        if data.get("code") != 0:
            raise RuntimeError(f"resolve video failed: {video_id}: {data.get('message')}")
        return int(data["data"]["aid"])

    def resolve_dynamic_target(self, raw: str) -> Target:
        dynamic_id = normalize_dynamic_id(raw)
        if not dynamic_id.isdigit():
            raise ValueError(f"dynamic id must be numeric: {raw}")

        data = self.get_json(API_DYNAMIC_DETAIL, self.sign_wbi_params({
            "id": dynamic_id,
            "timezone_offset": -480,
            "features": "itemOpusStyle",
            "dm_img_list": "[]",
            "dm_img_str": "V2ViR0wgMS",
            "dm_cover_img_str": "SW50ZWwoUikgSEQgR3JhcGhpY3NJbnRlbA",
        }))
        if data.get("code") != 0:
            raise RuntimeError(f"resolve dynamic failed: {dynamic_id}: {data.get('message')}")

        dyn_item = (data.get("data") or {}).get("item") or {}
        comment_id, comment_type = find_comment_params(dyn_item)
        if comment_id is None or comment_type is None:
            raise RuntimeError(f"resolve dynamic failed: cannot find comment oid/type for {dynamic_id}")

        return Target("dynamic", raw, int(comment_id), int(comment_type))

    def sign_wbi_params(self, params: dict[str, Any]) -> dict[str, Any]:
        mixin_key = self.get_wbi_mixin_key()
        signed = {k: v for k, v in params.items() if v is not None}
        signed["wts"] = int(time.time())

        filtered: dict[str, str] = {}
        for key, value in signed.items():
            text = str(value)
            text = re.sub(r"[!'()*]", "", text)
            filtered[str(key)] = text

        query = urlencode(sorted(filtered.items()))
        filtered["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return filtered

    def get_wbi_mixin_key(self) -> str:
        if self._wbi_mixin_key:
            return self._wbi_mixin_key

        data = self.get_json(API_NAV, {})
        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        img_key = extract_key_from_url(wbi_img.get("img_url") or "")
        sub_key = extract_key_from_url(wbi_img.get("sub_url") or "")
        raw_key = img_key + sub_key
        if len(raw_key) < 64:
            raise RuntimeError("cannot get WBI key from /x/web-interface/nav")

        self._wbi_mixin_key = "".join(raw_key[i] for i in WBI_MIXIN_KEY_ENC_TAB)[:32]
        return self._wbi_mixin_key

    def fetch_target(
        self,
        target: Target,
        page_size: int,
        reply_page_size: int,
        max_pages: int | None,
        max_reply_pages: int | None,
        reply_workers: int,
        sort: int,
        since_ts: int | None = None,
        until_ts: int | None = None,
    ) -> dict[str, Any]:
        comments: list[dict[str, Any]] = []
        page = 1
        pagination_str = ""
        total_count: int | None = None
        stopped_by_date = False

        while max_pages is None or page <= max_pages:
            payload = self.get_json(API_REPLY_WBI_MAIN, self.sign_wbi_params({
                "type": target.type_code,
                "oid": target.oid,
                "mode": reply_sort_to_mode(sort),
                "ps": page_size,
                "pagination_str": "" if page == 1 else make_pagination_str(pagination_str),
                "plat": 1,
                "seek_rpid": "",
                "web_location": 1315875,
            }))
            if payload.get("code") != 0:
                raise RuntimeError(f"comments failed: {target.kind}:{target.raw_id}: {payload.get('message')}")

            data = payload.get("data") or {}
            page_info = data.get("cursor") or data.get("page") or {}
            if total_count is None:
                total_count = page_info.get("all_count") or page_info.get("count")

            replies = data.get("replies") or []
            if not replies:
                break

            page_comments = [parse_comment(item) for item in replies]
            self._fill_nested_replies(
                target=target,
                comments=page_comments,
                reply_page_size=reply_page_size,
                max_reply_pages=max_reply_pages,
                reply_workers=reply_workers,
            )
            if since_ts is not None or until_ts is not None:
                page_comments = [filter_comment_by_time(c, since_ts, until_ts) for c in page_comments]
                page_comments = [c for c in page_comments if c is not None]
            comments.extend(page_comments)

            if sort == 0 and since_ts is not None:
                raw_times = [item.get("ctime") for item in replies if item.get("ctime") is not None]
                if raw_times and max(int(ts) for ts in raw_times) < since_ts:
                    stopped_by_date = True
                    break

            cursor = data.get("cursor") or {}
            pagination_reply = cursor.get("pagination_reply") or {}
            pagination_str = pagination_reply.get("next_offset") or ""
            if cursor.get("is_end") or not pagination_str:
                break
            page += 1

        return {
            "type": target.kind,
            "input_id": target.raw_id,
            "oid": target.oid,
            "comment_type_code": target.type_code,
            "total_count_reported": total_count,
            "fetched_count": len(comments),
            "stopped_by_date": stopped_by_date,
            "comments": comments,
        }

    def _fill_nested_replies(
        self,
        target: Target,
        comments: list[dict[str, Any]],
        reply_page_size: int,
        max_reply_pages: int | None,
        reply_workers: int,
    ) -> None:
        comments_need_fetch = [
            c for c in comments if c.get("rpid") and int(c.get("reply_count") or 0) > len(c.get("replies") or [])
        ]
        if not comments_need_fetch:
            return

        with ThreadPoolExecutor(max_workers=max(1, reply_workers)) as pool:
            future_map = {
                pool.submit(
                    self.fetch_replies,
                    target.type_code,
                    target.oid,
                    int(comment["rpid"]),
                    reply_page_size,
                    max_reply_pages,
                ): comment
                for comment in comments_need_fetch
            }
            for future in as_completed(future_map):
                comment = future_map[future]
                try:
                    comment["replies"] = future.result()
                except Exception as exc:
                    comment["reply_fetch_error"] = str(exc)

    def fetch_replies(
        self,
        type_code: int,
        oid: int,
        root_rpid: int,
        page_size: int,
        max_pages: int | None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1

        while max_pages is None or page <= max_pages:
            payload = self.get_json(
                API_REPLY_REPLY,
                {
                    "type": type_code,
                    "oid": oid,
                    "root": root_rpid,
                    "pn": page,
                    "ps": page_size,
                },
            )
            if payload.get("code") != 0:
                raise RuntimeError(f"nested replies failed: root={root_rpid}: {payload.get('message')}")

            data = payload.get("data") or {}
            replies = data.get("replies") or []
            if not replies:
                break

            collected.extend(parse_comment(item))
            if len(replies) < page_size:
                break
            page += 1

        return collected


def parse_comment(item: dict[str, Any]) -> dict[str, Any]:
    member = item.get("member") or {}
    content = item.get("content") or {}
    ctime = item.get("ctime")
    replies = item.get("replies") or []

    return {
        "rpid": item.get("rpid"),
        "root": item.get("root"),
        "parent": item.get("parent"),
        "username": member.get("uname"),
        "uid": str(member.get("mid")) if member.get("mid") is not None else None,
        "time": format_ts(ctime),
        "ctime": ctime,
        "reply": content.get("message") or "",
        "like": item.get("like"),
        "reply_count": item.get("rcount", 0),
        "replies": [parse_comment(reply) for reply in replies],
    }


def filter_comment_by_time(
    comment: dict[str, Any],
    since_ts: int | None,
    until_ts: int | None,
) -> dict[str, Any] | None:
    comment["replies"] = [
        filtered
        for reply in comment.get("replies", [])
        if (filtered := filter_comment_by_time(reply, since_ts, until_ts)) is not None
    ]

    ctime = comment.get("ctime")
    in_range = True
    if ctime is None:
        in_range = False
    if since_ts is not None and int(ctime or 0) < since_ts:
        in_range = False
    if until_ts is not None and int(ctime or 0) >= until_ts:
        in_range = False

    if in_range or comment["replies"]:
        return comment
    return None


def format_ts(value: Any) -> str | None:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def parse_day_range(day: str) -> tuple[int, int]:
    date_value = datetime.strptime(day, "%Y-%m-%d").date()
    start = datetime.combine(date_value, dt_time.min).astimezone()
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def reply_sort_to_mode(sort: int) -> int:
    # wbi/main uses mode instead of sort. mode=3 is Bilibili's common hot/default list.
    if sort == 0:
        return 2
    if sort == 2:
        return 3
    return 3


def make_pagination_str(offset: str) -> str:
    return json.dumps(
        {"type": 1, "direction": 1, "Data": {"offset": offset}},
        separators=(",", ":"),
    )


def normalize_video_id(raw: str) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.netloc:
        path = parsed.path.strip("/")
        bv_match = re.search(r"(BV[a-zA-Z0-9]+)", path)
        if bv_match:
            return bv_match.group(1)
        av_match = re.search(r"av(\d+)", path, re.IGNORECASE)
        if av_match:
            return f"av{av_match.group(1)}"
        qs = parse_qs(parsed.query)
        if "bvid" in qs:
            return qs["bvid"][0]
        if "aid" in qs:
            return qs["aid"][0]
    return value


def normalize_dynamic_id(raw: str) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.netloc:
        segments = [seg for seg in parsed.path.split("/") if seg]
        for segment in reversed(segments):
            if segment.isdigit():
                return segment
        qs = parse_qs(parsed.query)
        for key in ("id", "dynamic_id", "oid"):
            if key in qs:
                return qs[key][0]
    return value


def extract_key_from_url(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def find_comment_params(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        comment_id = first_present(value, ("comment_id", "comment_id_str", "comment_oid", "rid_str", "oid"))
        comment_type = first_present(value, ("comment_type", "comment_type_code", "type"))
        if is_int_like(comment_id) and is_int_like(comment_type):
            return int(comment_id), int(comment_type)

        modules = value.get("modules")
        if isinstance(modules, dict):
            module_stat = modules.get("module_stat")
            if isinstance(module_stat, dict):
                comment = module_stat.get("comment")
                if isinstance(comment, dict):
                    comment_id = first_present(comment, ("comment_id", "oid", "id"))
                    comment_type = first_present(comment, ("comment_type", "type"))
                    if is_int_like(comment_id) and is_int_like(comment_type):
                        return int(comment_id), int(comment_type)

        for child in value.values():
            found_id, found_type = find_comment_params(child)
            if found_id is not None and found_type is not None:
                return found_id, found_type

    if isinstance(value, list):
        for child in value:
            found_id, found_type = find_comment_params(child)
            if found_id is not None and found_type is not None:
                return found_id, found_type

    return None, None


def first_present(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def is_int_like(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def load_targets(args: argparse.Namespace, client: BilibiliClient) -> list[Target]:
    raw_targets: list[tuple[str, str]] = []
    for value in args.video:
        raw_targets.append(("video", value))
    for value in args.dynamic:
        raw_targets.append(("dynamic", value))

    if args.targets:
        with open(args.targets, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, list):
            raise ValueError("--targets JSON must be a list")
        for item in loaded:
            if not isinstance(item, dict) or "type" not in item or "id" not in item:
                raise ValueError("each target must be like {'type':'video|dynamic','id':'...'}")
            raw_targets.append((str(item["type"]), str(item["id"])))

    targets: list[Target] = []
    for kind, raw_id in raw_targets:
        kind = kind.lower()
        if kind in ("video", "v"):
            oid = client.resolve_video_oid(raw_id)
            targets.append(Target("video", raw_id, oid, TYPE_VIDEO))
        elif kind in ("dynamic", "dongtai", "dt"):
            targets.append(client.resolve_dynamic_target(raw_id))
        else:
            raise ValueError(f"unknown target type: {kind}")

    if not targets:
        raise ValueError("no targets, use --video/--dynamic/--targets")
    return targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="爬取 B 站视频/动态评论和评论回复，保存为 JSON。")
    parser.add_argument("--video", action="append", default=[], help="视频 BV号、av号、aid 或视频链接；可重复传入")
    parser.add_argument("--dynamic", action="append", default=[], help="动态 ID 或动态链接；可重复传入")
    parser.add_argument("--targets", help="批量目标 JSON 文件，格式为 [{'type':'video|dynamic','id':'...'}]")
    parser.add_argument("-o", "--output", required=True, help="输出 JSON 文件路径")
    parser.add_argument("--workers", type=int, default=4, help="目标级并发线程数，默认 4")
    parser.add_argument("--reply-workers", type=int, default=4, help="单个目标内拉取评论回复的线程数，默认 4")
    parser.add_argument("--page-size", type=int, default=20, help="一级评论每页数量，默认 20")
    parser.add_argument("--reply-page-size", type=int, default=20, help="评论回复每页数量，默认 20")
    parser.add_argument("--max-pages", type=int, help="每个目标最多爬取多少页一级评论；不传则尽量爬完")
    parser.add_argument("--max-reply-pages", type=int, help="每条评论最多爬取多少页回复；不传则尽量爬完")
    parser.add_argument("--sort", type=int, default=1, choices=[0, 1, 2], help="一级评论排序：0 时间，1 点赞，2 回复数；默认 1")
    parser.add_argument("--day", help="只保留指定日期的评论，格式 YYYY-MM-DD，按本机时区计算")
    parser.add_argument("--cookie", default=os.environ.get("BILI_COOKIE", ""), help="B站 Cookie；也可用环境变量 BILI_COOKIE")
    parser.add_argument("--timeout", type=float, default=15.0, help="请求超时时间秒数，默认 15")
    parser.add_argument("--retries", type=int, default=3, help="请求重试次数，默认 3")
    parser.add_argument("--min-delay", type=float, default=0.2, help="每次请求前最小等待秒数，默认 0.2")
    parser.add_argument("--max-delay", type=float, default=0.8, help="每次请求前最大等待秒数，默认 0.8")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    client = BilibiliClient(
        cookie=args.cookie,
        timeout=args.timeout,
        retries=args.retries,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
    )
    targets = load_targets(args, client)
    since_ts: int | None = None
    until_ts: int | None = None
    if args.day:
        since_ts, until_ts = parse_day_range(args.day)

    output: dict[str, Any] = {
        "source": "bilibili",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_count": len(targets),
        "filter": {
            "day": args.day,
            "since_ts": since_ts,
            "until_ts": until_ts,
        } if args.day else None,
        "targets": [],
    }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        future_map = {
            pool.submit(
                client.fetch_target,
                target,
                args.page_size,
                args.reply_page_size,
                args.max_pages,
                args.max_reply_pages,
                args.reply_workers,
                args.sort,
                since_ts,
                until_ts,
            ): target
            for target in targets
        }
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                result = future.result()
                print(f"OK {target.kind}:{target.raw_id} comments={result['fetched_count']}", file=sys.stderr)
            except Exception as exc:
                result = {
                    "type": target.kind,
                    "input_id": target.raw_id,
                    "oid": target.oid,
                    "comment_type_code": target.type_code,
                    "error": str(exc),
                    "comments": [],
                }
                print(f"ERR {target.kind}:{target.raw_id} {exc}", file=sys.stderr)
            output["targets"].append(result)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"saved: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
