#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
把 B 站评论 JSON 里的 username 和 uid 做部分打码。

示例：
  py .\mask_bilibili_users.py .\videos_2026-06-02_comments_main_only.json
  py .\mask_bilibili_users.py .\videos.json .\dynamics.json --output-dir .\masked
  py .\mask_bilibili_users.py .\comments.json -o .\comments_masked.json
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def mask_text(value: Any, keep_start: int, keep_end: int, mask_char: str) -> Any:
    if value is None:
        return value

    text = str(value)
    chars = list(text)
    if not chars:
        return text

    if len(chars) <= keep_start + keep_end:
        if len(chars) <= 1:
            return mask_char
        if len(chars) == 2:
            return chars[0] + mask_char
        return chars[0] + (mask_char * (len(chars) - 2)) + chars[-1]

    return (
        "".join(chars[:keep_start])
        + (mask_char * (len(chars) - keep_start - keep_end))
        + "".join(chars[-keep_end:] if keep_end else [])
    )


def mask_uid(value: Any, keep_start: int, keep_end: int, mask_char: str) -> Any:
    if value is None:
        return value
    return mask_text(str(value), keep_start, keep_end, mask_char)


def mask_comment_tree(
    comment: dict[str, Any],
    name_keep_start: int,
    name_keep_end: int,
    uid_keep_start: int,
    uid_keep_end: int,
    mask_char: str,
) -> None:
    if "username" in comment:
        comment["username"] = mask_text(comment["username"], name_keep_start, name_keep_end, mask_char)
    if "uid" in comment:
        comment["uid"] = mask_uid(comment["uid"], uid_keep_start, uid_keep_end, mask_char)

    for reply in comment.get("replies") or []:
        if isinstance(reply, dict):
            mask_comment_tree(
                reply,
                name_keep_start,
                name_keep_end,
                uid_keep_start,
                uid_keep_end,
                mask_char,
            )


def mask_document(data: Any, args: argparse.Namespace) -> Any:
    masked = deepcopy(data)
    for target in masked.get("targets", []) if isinstance(masked, dict) else []:
        for comment in target.get("comments", []) if isinstance(target, dict) else []:
            if isinstance(comment, dict):
                mask_comment_tree(
                    comment,
                    args.name_keep_start,
                    args.name_keep_end,
                    args.uid_keep_start,
                    args.uid_keep_end,
                    args.mask_char,
                )
    return masked


def output_path_for(input_path: Path, args: argparse.Namespace) -> Path:
    if args.output:
        if len(args.inputs) > 1:
            raise ValueError("-o/--output can only be used with one input file")
        return Path(args.output)

    if args.output_dir:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / f"{input_path.stem}_masked{input_path.suffix}"

    return input_path.with_name(f"{input_path.stem}_masked{input_path.suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把评论 JSON 中的 username 和 uid 做部分打码。")
    parser.add_argument("inputs", nargs="+", help="一个或多个评论 JSON 文件")
    parser.add_argument("-o", "--output", help="输出文件；只支持单输入文件时使用")
    parser.add_argument("--output-dir", help="多文件输出目录；默认输出到原文件旁边")
    parser.add_argument("--name-keep-start", type=int, default=1, help="用户名保留开头字符数，默认 1")
    parser.add_argument("--name-keep-end", type=int, default=1, help="用户名保留结尾字符数，默认 1")
    parser.add_argument("--uid-keep-start", type=int, default=2, help="uid 保留开头位数，默认 2")
    parser.add_argument("--uid-keep-end", type=int, default=2, help="uid 保留结尾位数，默认 2")
    parser.add_argument("--mask-char", default="*", help="打码字符，默认 *")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    for input_name in args.inputs:
        input_path = Path(input_name)
        with input_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        masked = mask_document(data, args)
        output_path = output_path_for(input_path, args)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(masked, f, ensure_ascii=False, indent=2)

        print(f"saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
