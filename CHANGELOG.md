# 2026-07-22 全量改动记录

## 15 个提交，23 个文件修改

---

## 第一轮: 内容质量修复

| 提交 | 改动 |
|------|------|
| `db0e7fd` | **配图去重+无关过滤+跨板块去重**: `image_fetcher.py` 同源同板块去重; `script_writer.py` 新增 `_validate_images()` 白名单+频次校验; `filter.py` 新增 `filter_topic_relevance()` 话题相关性关键词兜底; `classifier.py` prompt 排除自动驾驶/机器人/通用AI; `script_writer.py` `_cross_category_dedup` 新增产品名维度+中英品牌标准化; `device_os_map.py` 补充 mangmi air y |
| `c4f2059` | **台积电关键词过滤** |
| `73d1f19` | **SESSDATA 过期自动提醒**: `extract_sessdata.py` 半自动提取脚本; `bilibili_article_collector.py` 过期时写标记文件; `weekly.yml` CI 检测标记自动创建中文 Issue |
| `158842d` | **模拟经营游戏过滤**: 正则 `(?<![\w])[一-鿿]{2,8}模拟器[：:\s]` 区分 Simulator Game vs Emulator |
| `a775dc9` | **话题过滤加正面信号保护**: 含 Steam Deck/RTX/掌机关键词时不杀 |
| `fc54ec3` | **regex raw string 修复** |

## 第二轮: 传统主机拆分

| 提交 | 改动 |
|------|------|
| `0ae249a` | **console → playstation/xbox/nintendo**: 13个文件，config CATEGORIES 拆分、classifier prompt 重写、所有 collector 搜索关键词按品牌拆分、keyword_library 按品牌重组、RSS source category_hint 更新、tieba 映射更新、video_workflow CAT_LABEL_MAP 更新 |
| `d516c36` | **LLM返回旧key兜底**: `_reclassify_console()` 关键词映射; 收紧"索尼"关键词为"索尼主机""索尼掌机" |

## 第三轮: B站官方源优先 (方案A)

| 提交 | 改动 | 影响文件 |
|------|------|---------|
| `1b4b35b` | **7项改动合集** | 8 files |
| | ① CI 开启 `BILIBILI_IMAGE_RECOGNITION` (qwen-vl-max) | `weekly.yml` |
| | ② 识图触发扩展: 动态文字<30字+带图 → 自动视觉识别 | `bilibili_article_collector.py` |
| | ③ enrich.py: 厂商动态进补全 + 事件关键词防张冠李戴 + 搜索词加事件限定时词 | `enrich.py` |
| | ④ ranker: `bilibili_manufacturer` / `bilibili_dynamic` 加分 50→120，高于第三方博客 | `ranker.py` |
| | ⑤ 芒米全套接入: UID 3546721894271865 + 分类 linux→android 修正 + MANUFACTURER_SEARCHES + chinese_web/web_search 同步修正 | `bilibili_browser_collector.py`, `chinese_web.py`, `web_search.py` |
| | ⑥ "4. R" 空条目清理: `_normalize_format()` 中检测标题<5字符且无后续内容→删除 | `script_writer.py` |
| | ⑦ emulator 搜索词扩充: melonDS/Drastic/Dolphin/Cemu/Azahar 等15个具体模拟器名 | `bilibili_browser_collector.py`, `web_search.py` |
| `035c14e` | **CI 添加 OPENAI_VISION_MODEL/BASE_URL/API_KEY 环境变量** (3个GitHub Secrets) | `weekly.yml` |
| `68a1a5d` | **B站用户名脱敏**: CI日志只显示首字符+星号 | `bilibili_article_collector.py` |
| `e9fe20f` | **动态条目 `image_url` 赋值为首张图** (官方配图) | `bilibili_article_collector.py` |
| `9685958` | **图片本地化**: `_download_images()` 扫描 `![配图](URL)` → 下载到 `output/images/` → 替换为相对路径，带 B站 Referer 请求头 | `script_writer.py` |
| `a15c6dc` | **steam_deck 关键词加 steam machine** | `config.py` |
| `3bf8725` | **补充 steammachine 连写形式** | `config.py`, `bilibili_browser_collector.py` |

---

## 对视频工作流的影响

### 1. 板块名称变更
`video_workflow.py` `CAT_LABEL_MAP` 已更新：
```
"传统主机" → "playstation" / "xbox" / "任天堂 switch"
```

### 2. 配图路径变更 (⚠️ 待修复)
- **之前**: `![配图](https://i0.hdslb.com/bfs/archive/xxx.jpg)` — 远程URL
- **现在**: `![配图](images/abc123def456.jpg)` — 相对路径（相对于 `output/` 目录）

**影响**: Flask Web UI (`video_workflow.py`) 的 `<img src="{{ image_url }}">` 使用相对路径时会从 Flask 工作目录（项目根）查找，找不到 `output/images/` 下的文件。

**待修**: 需要添加 Flask 静态路由 `/images/<filename>` → `output/images/<filename>`

### 3. 采集量增加
方案A后每条周刊约 119 条精选，板块数从 7→9（+PlayStation/Xbox），视频工作流的板块选择 UI 需确认是否正常显示。

### 4. 源类型增加
新增 `bilibili_dynamic` 和 `bilibili_manufacturer` 作为 source_type，视频工作流解析时遇到这些来源不会报错（字段兼容）。

---

## 回收方案B（每日采集）
已存为备选计划，待方案A稳定后再实施。涉及 CI schedule 7天+2次、checkpoint 按日存储、合并去重逻辑。
