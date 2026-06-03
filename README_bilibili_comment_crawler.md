# Bilibili 评论爬取脚本

脚本文件：`bilibili_comment_crawler.py`

这个脚本用于抓取 B 站视频、动态的评论和评论回复，并保存为 UTF-8 JSON。

## 功能

- 支持视频：BV 号、av 号、aid、视频链接。
- 支持动态：动态 ID、动态链接。
- 支持多个目标并发抓取。
- 保存一级评论和评论下的回复。
- 记录用户名、uid、时间、评论/回复内容、点赞数、回复数、rpid、parent/root 等字段。
- 支持按日期过滤，例如只保留今天的评论。
- 动态会先解析真实评论区 `oid/type`，再抓评论。
- 主评论列表使用 B 站 WBI 游标分页接口，避免旧页码接口只返回少量热评。

## 运行环境

Windows 上建议用 `py` 运行，避免 `python` 命中 Microsoft Store 占位程序。

```powershell
py .\bilibili_comment_crawler.py --help
```

如果控制台中文或 emoji 显示异常，可以先设置：

```powershell
$env:PYTHONIOENCODING="utf-8"
```

JSON 文件本身会按 UTF-8 写入，不受控制台乱码影响。

## 基本用法

抓一个视频：

```powershell
py .\bilibili_comment_crawler.py --video BV1xx411c7mD -o .\comments.json
```

抓一个动态：

```powershell
py .\bilibili_comment_crawler.py --dynamic 1234567890123456789 -o .\dynamic_comments.json
```

多个目标一起抓：

```powershell
py .\bilibili_comment_crawler.py --video BV1xx411c7mD --dynamic 1234567890123456789 -o .\comments.json --workers 4
```

使用批量目标文件：

```powershell
py .\bilibili_comment_crawler.py --targets .\targets.example.json -o .\comments.json --workers 4
```

## 按日期过滤

只保留指定日期的评论，日期格式是 `YYYY-MM-DD`。当前脚本按本机时区计算日期边界。

```powershell
py .\bilibili_comment_crawler.py --video BV1xx411c7mD -o .\today_comments.json --day 2026-06-02
```

注意：日期过滤会保留“本体不在当天、但下面有当天回复”的父评论，以免丢失回复上下文。如果需要严格只保留本体时间在当天的记录，可以再做一次二次过滤。

## 增量更新

脚本支持读取已有 JSON，只抓新增评论，然后合并输出。推荐增量更新时使用时间排序：

```powershell
py .\bilibili_comment_crawler.py --dynamic 1209129105980653592 -o .\dynamics_all_comments_updated.json --incremental-existing .\dynamics_all_comments_main_only.json --merge-output --sort 0 --no-replies --workers 1 --retries 4 --min-delay 0 --max-delay 0
```

多个目标也可以一起更新：

```powershell
py .\bilibili_comment_crawler.py --dynamic 1209228787881869335? --dynamic 1209129105980653592 --dynamic 1209103404899500033 --dynamic 1208777348160159745 --dynamic 1206935842306654210 -o .\dynamics_all_comments_updated.json --incremental-existing .\dynamics_all_comments_main_only.json --merge-output --sort 0 --no-replies --workers 4 --retries 4 --min-delay 0 --max-delay 0
```

参数含义：

- `--incremental-existing`：已有 JSON 文件。
- `--merge-output`：把新增评论合并进已有评论后写入 `-o` 指定的新文件。
- `--sort 0`：按时间顺序抓取，遇到已有 `rpid` 就停止，适合更新。
- `--no-replies`：只更新一级评论。如果要同时抓楼中楼回复，可以去掉这个参数，但会慢很多。

注意：如果旧文件之前是用热门排序抓的，第一次改用 `--sort 0` 更新时可能会补到一批旧文件没有覆盖的评论；之后再更新就会很快停止。

## 这次任务的命令

两个视频只抓今天评论：

```powershell
py .\bilibili_comment_crawler.py --video BV1JeL86xEEd --video BV1PJ8XzLEKB -o .\videos_2026-06-02_comments.json --day 2026-06-02 --sort 1 --workers 2 --reply-workers 6 --retries 8 --min-delay 0.8 --max-delay 1.5
```

四个动态不按时间过滤，直接全量抓：

```powershell
py .\bilibili_comment_crawler.py --dynamic 1209129105980653592 --dynamic 1209103404899500033 --dynamic 1208777348160159745 --dynamic 1206935842306654210 -o .\dynamics_all_comments.json --sort 1 --workers 2 --reply-workers 6 --retries 8 --min-delay 0.8 --max-delay 1.5
```

## 小范围测试

只抓前 1 页评论、每条评论最多 1 页回复：

```powershell
py .\bilibili_comment_crawler.py --video BV1xx411c7mD -o .\test.json --max-pages 1 --max-reply-pages 1
```

只抓前 3 页主评论：

```powershell
py .\bilibili_comment_crawler.py --dynamic 1209129105980653592 -o .\tmp_wbi_test.json --max-pages 3
```

## Cookie

部分动态、登录可见内容或风控场景需要 Cookie。可以直接传：

```powershell
py .\bilibili_comment_crawler.py --video BV1xx411c7mD -o .\comments.json --cookie "SESSDATA=xxx; bili_jct=xxx"
```

也可以设置环境变量：

```powershell
$env:BILI_COOKIE="SESSDATA=xxx; bili_jct=xxx"
py .\bilibili_comment_crawler.py --video BV1xx411c7mD -o .\comments.json
```

## 常用参数

- `--workers 4`：多个视频/动态之间的并发数。
- `--reply-workers 6`：单个目标内抓评论回复的并发数。
- `--max-pages 10`：每个目标最多抓 10 页一级评论，不传则尽量抓完。
- `--max-reply-pages 5`：每条评论最多抓 5 页回复，不传则尽量抓完。
- `--day 2026-06-02`：只保留指定日期的评论/回复。
- `--sort 1`：一级评论排序。`0` 时间，`1` 点赞/默认热门，`2` 回复数。部分目标在旧时间排序下会返回空页，建议默认用 `1`。
- `--incremental-existing old.json --merge-output`：读取旧文件，只抓新增并合并输出。
- `--retries 8`：请求失败或遇到 `-352/-412/-509` 时的重试次数。
- `--min-delay 0.8 --max-delay 1.5`：每次请求前随机等待，降低风控概率。
- `--cookie "..."`：传 B站 Cookie。

## 输出结构

```json
{
  "source": "bilibili",
  "generated_at": "2026-06-02T12:00:00+08:00",
  "target_count": 1,
  "filter": {
    "day": "2026-06-02",
    "since_ts": 1780329600,
    "until_ts": 1780416000
  },
  "targets": [
    {
      "type": "video",
      "input_id": "BV...",
      "oid": 123,
      "comment_type_code": 1,
      "total_count_reported": 100,
      "fetched_count": 20,
      "comments": [
        {
          "rpid": 123456,
          "root": 0,
          "parent": 0,
          "username": "用户名",
          "uid": "123456",
          "time": "2026-06-02 12:00:00+0800",
          "ctime": 1780372800,
          "reply": "评论内容",
          "like": 10,
          "reply_count": 2,
          "replies": []
        }
      ]
    }
  ]
}
```

## 注意事项

- 大量抓取时建议降低并发，增加 delay，必要时带 Cookie。
- B站接口可能返回 `-352`、`-412`、`-509`，脚本会自动重试，但不能保证完全绕过风控。
- 动态 ID 不一定是评论区 `oid`。脚本会先调用动态详情接口解析真实 `comment_id/comment_type`。
- 输出文件在整个目标抓完后一次性写入；长任务运行期间文件不会持续更新。
